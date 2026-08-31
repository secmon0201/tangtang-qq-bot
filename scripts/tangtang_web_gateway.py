"""Expose the bot's approved web surfaces behind one fixed public hostname."""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import re
import threading
from dataclasses import dataclass
from email.utils import formatdate
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
COPY_CHUNK_BYTES = 64 * 1024
UPSTREAM_TIMEOUT_SECONDS = 180
DEFAULT_MAX_CONCURRENCY = 32
DEFAULT_CLIENT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024
OPERATOR_KINDS = frozenset({"activity", "operations", "duplicate"})
SHORT_LINK_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "public-short-links.json"
SITE_ROOT = (Path(__file__).resolve().parents[1] / "site").resolve()
PUBLIC_SITE_RELEASES_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "public-site-releases"
).resolve()
PUBLIC_SITE_POINTER = Path(__file__).resolve().parents[1] / "data" / "public-site-current.txt"
PUBLIC_SITE_MANIFEST = "_site-manifest.json"
RELEASE_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-f0-9]{12}$")
FINGERPRINTED_FILE_PATTERN = re.compile(r"\.[a-f0-9]{12}\.[A-Za-z0-9]+$")
SITE_ROOT_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}
SITE_PAGE_ROUTES = {
    "/experience": "experience/index.html",
    "/experience/": "experience/index.html",
    "/games": "games/index.html",
    "/games/": "games/index.html",
    "/community": "community/index.html",
    "/community/": "community/index.html",
    "/operator": "operator/index.html",
    "/operator/": "operator/index.html",
    "/technology": "technology/index.html",
    "/technology/": "technology/index.html",
    "/release": "release/index.html",
    "/release/": "release/index.html",
}
SITE_ASSET_PREFIXES = ("/assets/", "/vendor/")
STATIC_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
_POINTER_CACHE_LOCK = threading.Lock()
_POINTER_CACHE_KEY: tuple[str, str, str] | None = None
_POINTER_CACHE_SIGNATURE: tuple[int, int, int, int] | None = None
_POINTER_CACHE_ROOT = SITE_ROOT


def _positive_env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _pointer_signature() -> tuple[int, int, int, int] | None:
    try:
        stat = PUBLIC_SITE_POINTER.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_size


def _resolve_active_site_root() -> Path:
    try:
        release_id = PUBLIC_SITE_POINTER.read_text(encoding="ascii").strip()
    except OSError:
        return SITE_ROOT
    if not RELEASE_ID_PATTERN.fullmatch(release_id):
        return SITE_ROOT
    candidate = (PUBLIC_SITE_RELEASES_ROOT / release_id).resolve()
    try:
        candidate.relative_to(PUBLIC_SITE_RELEASES_ROOT)
    except ValueError:
        return SITE_ROOT
    if not (candidate / PUBLIC_SITE_MANIFEST).is_file():
        return SITE_ROOT
    return candidate


def active_site_root() -> Path:
    """Resolve the active release with one cheap stat per request and cached reads."""
    global _POINTER_CACHE_KEY, _POINTER_CACHE_ROOT, _POINTER_CACHE_SIGNATURE
    cache_key = (
        str(PUBLIC_SITE_POINTER),
        str(PUBLIC_SITE_RELEASES_ROOT),
        str(SITE_ROOT),
    )
    signature = _pointer_signature()
    if cache_key == _POINTER_CACHE_KEY and signature == _POINTER_CACHE_SIGNATURE:
        return _POINTER_CACHE_ROOT
    with _POINTER_CACHE_LOCK:
        signature = _pointer_signature()
        if cache_key != _POINTER_CACHE_KEY or signature != _POINTER_CACHE_SIGNATURE:
            _POINTER_CACHE_ROOT = _resolve_active_site_root()
            _POINTER_CACHE_KEY = cache_key
            _POINTER_CACHE_SIGNATURE = signature
        return _POINTER_CACHE_ROOT


@lru_cache(maxsize=32)
def public_site_routes(site_root: Path) -> dict[str, str]:
    """Load the route allowlist that belongs to the active, complete release."""
    manifest_path = site_root / PUBLIC_SITE_MANIFEST
    if not manifest_path.is_file():
        return SITE_ROOT_FILES | SITE_PAGE_ROUTES
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        routes = payload["public_routes"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}
    if not isinstance(routes, dict):
        return {}
    safe_routes = {}
    for route, relative_name in routes.items():
        if not isinstance(route, str) or not route.startswith("/"):
            continue
        if not isinstance(relative_name, str):
            continue
        if "\\" in relative_name:
            continue
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        safe_routes[route] = relative_name
    return safe_routes


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    target_port: int
    upstream_target: str


def load_short_links() -> tuple[str, dict[str, str]]:
    """Load the explicit ASCII short-link allowlist."""
    try:
        payload = json.loads(SHORT_LINK_CONFIG_PATH.read_text(encoding="utf-8"))
        host = str(payload["host"]).lower()
        links = payload["links"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("public short-link configuration is invalid") from exc
    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise RuntimeError("public short-link host is invalid")
    redirects: dict[str, str] = {}
    for code, item in links.items():
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", code):
            raise RuntimeError("public short-link code is invalid")
        if not isinstance(item, dict):
            raise RuntimeError("public short-link entry is invalid")
        target = item.get("target")
        parsed = urlsplit(str(target or ""))
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("public short-link target must be HTTPS")
        redirects[f"/{code}"] = str(target)
    return host, redirects


SHORT_LINK_HOST, SHORT_LINK_REDIRECTS = load_short_links()


def _request_hostname(raw_host: str) -> str:
    return raw_host.partition(":")[0].strip().lower()


def short_redirect_target(raw_host: str, raw_target: str) -> str | None:
    if _request_hostname(raw_host) != SHORT_LINK_HOST:
        return None
    return SHORT_LINK_REDIRECTS.get(urlsplit(raw_target).path)


def resolve_public_site_file(raw_target: str) -> Path | None:
    """Resolve the small public-site allowlist without exposing repository files."""
    site_root = active_site_root()
    decoded_path = unquote(urlsplit(raw_target).path)
    if decoded_path.endswith((".br", ".gz")):
        return None
    relative_name = public_site_routes(site_root).get(decoded_path)
    if relative_name is None and decoded_path in SITE_ROOT_FILES:
        relative_name = SITE_ROOT_FILES[decoded_path]
    if relative_name is None:
        if "\\" in decoded_path or not decoded_path.startswith(SITE_ASSET_PREFIXES):
            return None
        relative_name = decoded_path.lstrip("/")

    relative_path = Path(relative_name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    candidate = (site_root / relative_path).resolve()
    try:
        candidate.relative_to(site_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _accepted_encoding(header: str, path: Path) -> tuple[Path, str | None]:
    qualities: dict[str, float] = {}
    for item in header.lower().split(","):
        parts = [part.strip() for part in item.split(";") if part.strip()]
        if not parts:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        qualities[parts[0]] = max(0.0, min(quality, 1.0))
    candidates = []
    for encoding, suffix, preference in (("br", ".br", 2), ("gzip", ".gz", 1)):
        quality = qualities.get(encoding, qualities.get("*", 0.0))
        candidate = path.with_name(path.name + suffix)
        if quality > 0 and candidate.is_file():
            candidates.append((quality, preference, candidate, encoding))
    if not candidates:
        return path, None
    _, _, selected, encoding = max(candidates)
    return selected, encoding


def _cache_control(path: Path) -> str:
    if path.suffix.lower() == ".html":
        return "no-cache, max-age=0, must-revalidate"
    if FINGERPRINTED_FILE_PATTERN.search(path.name):
        return "public, max-age=31536000, immutable"
    if path.parts[-2] in {"assets", "vendor"}:
        return "public, max-age=3600, must-revalidate"
    return "public, max-age=300, must-revalidate"


def _etag(path: Path, encoding: str | None) -> str:
    stat = path.stat()
    representation = encoding or "identity"
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}-{representation}"'


def route_public_target(raw_target: str) -> Route | None:
    """Map one public path to its internal route without exposing other APIs."""
    parsed = urlsplit(raw_target)
    segments = parsed.path.lstrip("/").split("/", 1)
    public_area = segments[0]
    remainder = segments[1] if len(segments) == 2 else ""

    if public_area == "notice" and remainder:
        upstream = f"/announcement/{remainder}"
        return Route("notice", 8080, urlunsplit(("", "", upstream, parsed.query, "")))
    if public_area in OPERATOR_KINDS and remainder:
        if remainder.startswith("api/"):
            upstream = f"/operator/api/{public_area}/{remainder[4:]}"
            query = parsed.query
        else:
            upstream = f"/operator/{remainder}"
            query_items = parse_qsl(parsed.query, keep_blank_values=True)
            query_items = [(key, value) for key, value in query_items if key != "kind"]
            query_items.append(("kind", public_area))
            query = urlencode(query_items)
        return Route(public_area, 8080, urlunsplit(("", "", upstream, query, "")))
    if public_area == "nte" and remainder:
        return Route("nte", 8765, urlunsplit(("", "", parsed.path, parsed.query, "")))
    if public_area == "live":
        upstream = "/asoul-live/" + remainder
        return Route("live", 8080, urlunsplit(("", "", upstream, parsed.query, "")))
    if public_area in {"ranking", "help"}:
        upstream = f"/community/{public_area}/" + remainder
        return Route(public_area, 8080, urlunsplit(("", "", upstream, parsed.query, "")))
    return None


class TangtangWebGateway(BaseHTTPRequestHandler):
    server_version = "tangtang-web-gateway/2.0"
    protocol_version = "HTTP/1.1"

    def _request_length(self) -> int | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            self.close_connection = True
            return None
        if length < 0:
            self.send_error(400, "invalid Content-Length")
            self.close_connection = True
            return None
        max_request_bytes = getattr(self.server, "max_request_bytes", DEFAULT_MAX_REQUEST_BYTES)
        if length > max_request_bytes:
            self.send_error(413, "request body too large")
            self.close_connection = True
            return None
        return length

    def handle_expect_100(self) -> bool:
        length = self._request_length()
        if length is None:
            return False
        return super().handle_expect_100()

    def _serve_site_file(self, path: Path) -> None:
        content_type, _ = mimetypes.guess_type(path.name)
        served_path, encoding = _accepted_encoding(self.headers.get("Accept-Encoding", ""), path)
        stat = served_path.stat()
        etag = _etag(served_path, encoding)
        cache_control = _cache_control(path)
        request_etags = {
            item.strip() for item in self.headers.get("If-None-Match", "").split(",") if item.strip()
        }
        not_modified = etag in request_etags or "*" in request_etags
        self.send_response(304 if not_modified else 200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Cache-Control", cache_control)
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        if path.with_name(path.name + ".br").is_file() or path.with_name(path.name + ".gz").is_file():
            self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        if not not_modified:
            self.send_header("Content-Length", str(stat.st_size))
        for key, value in STATIC_SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command == "HEAD" or not_modified:
            return
        with served_path.open("rb") as source:
            while chunk := source.read(COPY_CHUNK_BYTES):
                self.wfile.write(chunk)

    def _serve_short_redirect(self, target: str) -> None:
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        for key, value in STATIC_SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()

    def _forward(self) -> None:
        request_host = self.headers.get("Host", "")
        if _request_hostname(request_host) == SHORT_LINK_HOST:
            target = short_redirect_target(request_host, self.path)
            if self.command in {"GET", "HEAD"} and target is not None:
                self._serve_short_redirect(target)
            else:
                self.send_error(404)
            return

        if self.command in {"GET", "HEAD"}:
            site_file = resolve_public_site_file(self.path)
            if site_file is not None:
                self._serve_site_file(site_file)
                return

        route = route_public_target(self.path)
        if route is None:
            self.send_error(404)
            return

        if self.headers.get("Transfer-Encoding"):
            self.send_error(501, "chunked request bodies are not supported")
            self.close_connection = True
            return
        length = self._request_length()
        if length is None:
            return
        connection = http.client.HTTPConnection(
            "127.0.0.1", route.target_port, timeout=UPSTREAM_TIMEOUT_SECONDS
        )
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS | {"host"}
        }
        try:
            connection.putrequest(self.command, route.upstream_target, skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{route.target_port}")
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(COPY_CHUNK_BYTES, remaining))
                if not chunk:
                    raise OSError("request body ended before Content-Length")
                connection.send(chunk)
                remaining -= len(chunk)
            response = connection.getresponse()
        except OSError:
            connection.close()
            self.send_error(503, f"{route.name} service unavailable")
            return

        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP_HEADERS | {"content-length"}:
                self.send_header(key, value)
        response_length = response.getheader("Content-Length")
        if response_length is not None:
            self.send_header("Content-Length", response_length)
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        try:
            while chunk := response.read(COPY_CHUNK_BYTES):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()

    do_GET = _forward
    do_HEAD = _forward
    do_POST = _forward

    def log_message(self, format: str, *args) -> None:
        return


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Keep concurrent clients independent without allowing unbounded threads."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(
        self,
        server_address,
        request_handler_class,
        *,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        client_timeout_seconds: int = DEFAULT_CLIENT_TIMEOUT_SECONDS,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    ) -> None:
        if min(max_concurrency, client_timeout_seconds, max_request_bytes) <= 0:
            raise ValueError("gateway limits must be positive")
        self.max_concurrency = max_concurrency
        self.client_timeout_seconds = client_timeout_seconds
        self.max_request_bytes = max_request_bytes
        self._request_slots = threading.BoundedSemaphore(max_concurrency)
        super().__init__(server_address, request_handler_class)

    def get_request(self):
        request, client_address = super().get_request()
        request.settimeout(self.client_timeout_seconds)
        return request, client_address

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(timeout=0.25):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Length: 0\r\nRetry-After: 1\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18769)
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=_positive_env_int("TANGTANG_WEB_MAX_CONCURRENCY", DEFAULT_MAX_CONCURRENCY),
    )
    parser.add_argument(
        "--client-timeout-seconds",
        type=int,
        default=_positive_env_int("TANGTANG_WEB_CLIENT_TIMEOUT_SECONDS", DEFAULT_CLIENT_TIMEOUT_SECONDS),
    )
    parser.add_argument(
        "--max-request-mb",
        type=int,
        default=_positive_env_int("TANGTANG_WEB_MAX_REQUEST_MB", DEFAULT_MAX_REQUEST_BYTES // (1024 * 1024)),
    )
    args = parser.parse_args()
    server = BoundedThreadingHTTPServer(
        ("127.0.0.1", args.port),
        TangtangWebGateway,
        max_concurrency=args.max_concurrency,
        client_timeout_seconds=args.client_timeout_seconds,
        max_request_bytes=args.max_request_mb * 1024 * 1024,
    )
    print(
        f"tangtang web gateway listening on http://127.0.0.1:{args.port} "
        f"with max_concurrency={args.max_concurrency}, "
        f"client_timeout={args.client_timeout_seconds}s, max_request={args.max_request_mb}MiB "
        "-> short links, site, live, ranking, help, notice, activity, operations, duplicate, nte",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
