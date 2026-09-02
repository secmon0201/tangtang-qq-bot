from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from http.client import HTTPConnection
from pathlib import Path

import brotli
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def load_gateway():
    path = ROOT / "scripts" / "tangtang_web_gateway.py"
    spec = importlib.util.spec_from_file_location("tangtang_web_gateway_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_builder():
    path = ROOT / "scripts" / "build_public_site.py"
    spec = importlib.util.spec_from_file_location("build_public_site_gateway_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built_public_site(tmp_path_factory):
    builder = load_builder()
    output = tmp_path_factory.mktemp("public-site") / "site"
    return output, builder.build_site(output)


def configure_site_root(gateway, site_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(gateway, "SITE_ROOT", site_root.resolve())
    monkeypatch.setattr(gateway, "PUBLIC_SITE_POINTER", tmp_path / "missing-pointer.txt")
    monkeypatch.setattr(gateway, "PUBLIC_SITE_RELEASES_ROOT", (tmp_path / "releases").resolve())
    gateway.public_site_routes.cache_clear()
    gateway._POINTER_CACHE_KEY = None
    gateway._POINTER_CACHE_SIGNATURE = None
    gateway._POINTER_CACHE_ROOT = site_root.resolve()


def start_gateway(gateway, **limits):
    server = gateway.BoundedThreadingHTTPServer(
        ("127.0.0.1", 0), gateway.TangtangWebGateway, **limits
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_gateway(server, thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_public_routes_are_explicit_and_rewritten():
    gateway = load_gateway()

    assert gateway.route_public_target("/notice/token").upstream_target == "/announcement/token"
    assert gateway.route_public_target("/notice/api/token/state").upstream_target == "/announcement/api/token/state"
    assert gateway.route_public_target("/wife/token") is None
    assert gateway.route_public_target("/activity/token") is None
    assert gateway.route_public_target("/operations/api/token/state") is None
    assert gateway.route_public_target("/duplicate/api/token/scan").upstream_target == "/operator/api/duplicate/token/scan"
    assert gateway.route_public_target("/nte/i/token").upstream_target == "/nte/i/token"
    assert gateway.route_public_target("/live/?view=tomorrow").upstream_target == "/asoul-live/?view=tomorrow"
    assert gateway.route_public_target("/live/api/schedule?view=week").upstream_target == "/asoul-live/api/schedule?view=week"
    assert gateway.route_public_target("/ranking/token/?scope=week").upstream_target == "/ranking/token/?scope=week"
    assert gateway.route_public_target("/ranking/token/api?scope=day").upstream_target == "/ranking/token/api?scope=day"
    assert gateway.route_public_target("/help/").upstream_target == "/help/"
    assert gateway.route_public_target("/help/api").upstream_target == "/help/api"


def test_private_and_unknown_routes_are_blocked():
    gateway = load_gateway()

    for path in ("/", "/api/send_msg", "/ws/QQLocalDataBot", "/internal/codex", "/notice", "/operator/token"):
        assert gateway.route_public_target(path) is None


def test_root_domain_short_links_are_explicit_redirects():
    gateway = load_gateway()

    assert gateway.SHORT_LINK_REDIRECTS == {
        "/r": "https://tangtang.secmon.cn/live/?view=week",
        "/h": "https://tangtang.secmon.cn/help/",
    }
    assert gateway.short_redirect_target("s.secmon.cn", "/r") == "https://tangtang.secmon.cn/live/?view=week"
    assert gateway.short_redirect_target("S.SECMON.CN:443", "/r?source=qq") == "https://tangtang.secmon.cn/live/?view=week"
    assert gateway.short_redirect_target("s.secmon.cn", "/s") is None
    assert gateway.short_redirect_target("s.secmon.cn", "/h") == "https://tangtang.secmon.cn/help/"
    assert gateway.short_redirect_target("s.secmon.cn", "/t") is None
    assert gateway.short_redirect_target("secmon.cn", "/r") is None
    assert gateway.short_redirect_target("tangtang.secmon.cn", "/r") is None


def test_root_domain_serves_only_short_redirects():
    gateway = load_gateway()
    server, thread = start_gateway(gateway)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/r", headers={"Host": "s.secmon.cn"})
        response = connection.getresponse()
        response.read()
        assert response.status == 302
        assert response.getheader("Location") == "https://tangtang.secmon.cn/live/?view=week"
        assert response.getheader("Cache-Control") == "no-store"
        connection.close()

        for path in ("/", "/t", "/m", "/w", "/api/send_msg", "/unknown"):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", path, headers={"Host": "s.secmon.cn"})
            response = connection.getresponse()
            response.read()
            assert response.status == 404
            connection.close()
    finally:
        stop_gateway(server, thread)


def test_public_site_routes_are_allowlisted_without_path_traversal(
    tmp_path, monkeypatch, built_public_site
):
    built, manifest = built_public_site
    gateway = load_gateway()
    configure_site_root(gateway, built, tmp_path, monkeypatch)

    assert gateway.resolve_public_site_file("/") == built / "index.html"
    for page in ("experience", "games", "operator", "technology", "release"):
        assert gateway.resolve_public_site_file(f"/{page}/") == built / page / "index.html"
        assert gateway.resolve_public_site_file(f"/{page}") == built / page / "index.html"
    assert gateway.resolve_public_site_file("/community/") is None
    assert gateway.resolve_public_site_file("/community") is None
    for original_url in (
        "/styles.css",
        "/assets/tangtang-avatar.jpg",
        "/vendor/lucide.min.js",
    ):
        built_url = manifest["static_assets"][original_url]
        assert gateway.resolve_public_site_file(built_url).is_file()
    for path in (
        "/README.md",
        "/_headers",
        "/assets/../README.md",
        "/assets/%2e%2e/README.md",
        "/assets/..\\README.md",
        "/experience/README.md",
        "/games/../README.md",
        "/unknown/",
    ):
        assert gateway.resolve_public_site_file(path) is None


def test_complete_release_switches_routes_with_one_pointer(tmp_path, monkeypatch):
    gateway = load_gateway()
    releases = tmp_path / "releases"
    release_id = "20260829-120000-abcdef123456"
    release = releases / release_id
    (release / "notes").mkdir(parents=True)
    (release / "index.html").write_text("new home", encoding="utf-8")
    (release / "notes" / "index.html").write_text("new notes", encoding="utf-8")
    (release / "_site-manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "public_routes": {
                    "/": "index.html",
                    "/notes": "notes/index.html",
                    "/notes/": "notes/index.html",
                },
            }
        ),
        encoding="utf-8",
    )
    pointer = tmp_path / "current.txt"
    pointer.write_text(release_id + "\n", encoding="ascii")
    monkeypatch.setattr(gateway, "PUBLIC_SITE_RELEASES_ROOT", releases.resolve())
    monkeypatch.setattr(gateway, "PUBLIC_SITE_POINTER", pointer)

    assert gateway.active_site_root() == release.resolve()
    assert gateway.resolve_public_site_file("/") == release.resolve() / "index.html"
    assert gateway.resolve_public_site_file("/notes/") == release.resolve() / "notes" / "index.html"
    assert gateway.resolve_public_site_file("/_site-manifest.json") is None

    next_release_id = "20260829-120001-fedcba654321"
    next_release = releases / next_release_id
    next_release.mkdir(parents=True)
    (next_release / "index.html").write_text("next home", encoding="utf-8")
    (next_release / "_site-manifest.json").write_text(
        json.dumps({"version": 2, "public_routes": {"/": "index.html"}}),
        encoding="utf-8",
    )
    replacement = tmp_path / "current.next"
    replacement.write_text(next_release_id + "\n", encoding="ascii")
    os.replace(replacement, pointer)

    assert gateway.active_site_root() == next_release.resolve()
    assert gateway.resolve_public_site_file("/") == next_release.resolve() / "index.html"
    assert gateway.resolve_public_site_file("/notes/") is None


def test_incomplete_or_invalid_release_pointer_falls_back_to_local_site(tmp_path, monkeypatch):
    gateway = load_gateway()
    fallback = (tmp_path / "compat-site").resolve()
    fallback.mkdir()
    (fallback / "index.html").write_text("fallback home", encoding="utf-8")
    pointer = tmp_path / "current.txt"
    pointer.write_text("../site\n", encoding="ascii")
    monkeypatch.setattr(gateway, "SITE_ROOT", fallback)
    monkeypatch.setattr(gateway, "PUBLIC_SITE_RELEASES_ROOT", (tmp_path / "releases").resolve())
    monkeypatch.setattr(gateway, "PUBLIC_SITE_POINTER", pointer)
    gateway.public_site_routes.cache_clear()
    gateway._POINTER_CACHE_KEY = None
    gateway._POINTER_CACHE_SIGNATURE = None
    gateway._POINTER_CACHE_ROOT = fallback

    assert gateway.active_site_root() == fallback
    assert gateway.resolve_public_site_file("/") == fallback / "index.html"


def test_gateway_serves_homepage_and_keeps_private_paths_blocked(
    tmp_path, monkeypatch, built_public_site
):
    built, _ = built_public_site
    gateway = load_gateway()
    configure_site_root(gateway, built, tmp_path, monkeypatch)
    server, thread = start_gateway(gateway)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        body = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/html"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert "糖糖" in body.decode("utf-8")
        connection.close()

        for page in ("experience", "games", "operator", "technology", "release"):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", f"/{page}/")
            response = connection.getresponse()
            body = response.read()
            assert response.status == 200
            assert response.getheader("Content-Type") == "text/html"
            assert response.getheader("Cache-Control") == "no-cache, max-age=0, must-revalidate"
            assert "糖糖" in body.decode("utf-8")
            connection.close()

        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/community/")
        response = connection.getresponse()
        response.read()
        assert response.status == 404
        connection.close()

        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/api/send_msg")
        response = connection.getresponse()
        response.read()
        assert response.status == 404
        connection.close()
    finally:
        stop_gateway(server, thread)


def test_http11_connection_is_reused_for_multiple_static_requests(
    tmp_path, monkeypatch, built_public_site
):
    built, _ = built_public_site
    gateway = load_gateway()
    configure_site_root(gateway, built, tmp_path, monkeypatch)
    server, thread = start_gateway(gateway)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/")
        first = connection.getresponse()
        first.read()
        first_socket = connection.sock
        assert first.version == 11
        assert first_socket is not None

        connection.request("GET", "/technology/")
        second = connection.getresponse()
        second.read()
        assert second.status == 200
        assert connection.sock is first_socket
        connection.close()
    finally:
        stop_gateway(server, thread)


def test_static_compression_cache_headers_and_representation_etags(tmp_path, monkeypatch):
    builder = load_builder()
    built = tmp_path / "built"
    manifest = builder.build_site(built)
    gateway = load_gateway()
    configure_site_root(gateway, built, tmp_path, monkeypatch)
    style_url = manifest["static_assets"]["/styles.css"]
    identity = (built / style_url.lstrip("/")).read_bytes()
    server, thread = start_gateway(gateway)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", style_url, headers={"Accept-Encoding": "gzip, br"})
        response = connection.getresponse()
        compressed = response.read()
        brotli_etag = response.getheader("ETag")
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/css"
        assert response.getheader("Content-Encoding") == "br"
        assert response.getheader("Vary") == "Accept-Encoding"
        assert response.getheader("Cache-Control") == "public, max-age=31536000, immutable"
        assert brotli.decompress(compressed) == identity

        connection.request(
            "GET",
            style_url,
            headers={"Accept-Encoding": "br", "If-None-Match": brotli_etag},
        )
        cached = connection.getresponse()
        assert cached.status == 304
        assert cached.read() == b""

        connection.request("GET", style_url, headers={"Accept-Encoding": "gzip"})
        gzip_response = connection.getresponse()
        gzip_response.read()
        assert gzip_response.status == 200
        assert gzip_response.getheader("Content-Encoding") == "gzip"
        assert gzip_response.getheader("ETag") != brotli_etag

        connection.request("GET", style_url)
        identity_response = connection.getresponse()
        assert identity_response.read() == identity
        assert identity_response.getheader("Content-Encoding") is None
        assert identity_response.getheader("ETag") not in {brotli_etag, gzip_response.getheader("ETag")}

        connection.request("GET", style_url + ".br")
        blocked = connection.getresponse()
        blocked.read()
        assert blocked.status == 404
        connection.close()
    finally:
        stop_gateway(server, thread)


def test_gateway_rejects_oversized_request_before_proxying():
    gateway = load_gateway()
    server, thread = start_gateway(gateway, max_request_bytes=4)
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/notice/token", body=b"12345")
        response = connection.getresponse()
        response.read()
        assert response.status == 413
        connection.close()
    finally:
        stop_gateway(server, thread)


def test_bounded_server_returns_503_when_all_request_slots_are_busy():
    gateway = load_gateway()
    entered = threading.Event()
    release = threading.Event()
    first_status: list[int] = []

    class BlockingHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            entered.set()
            release.wait(timeout=5)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = gateway.BoundedThreadingHTTPServer(
        ("127.0.0.1", 0), BlockingHandler, max_concurrency=1
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    def first_request() -> None:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/hold")
        response = connection.getresponse()
        first_status.append(response.status)
        response.read()
        connection.close()

    request_thread = threading.Thread(target=first_request)
    request_thread.start()
    try:
        assert entered.wait(timeout=5)
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        started = time.perf_counter()
        connection.request("GET", "/overload")
        response = connection.getresponse()
        response.read()
        assert response.status == 503
        assert response.getheader("Retry-After") == "1"
        assert time.perf_counter() - started < 2
        connection.close()
    finally:
        release.set()
        request_thread.join(timeout=5)
        stop_gateway(server, server_thread)
    assert first_status == [200]


def test_gateway_streams_request_and_response_bodies():
    source = (ROOT / "scripts" / "tangtang_web_gateway.py").read_text(encoding="utf-8")

    assert "COPY_CHUNK_BYTES = 64 * 1024" in source
    assert "while remaining:" in source
    assert "response.read(COPY_CHUNK_BYTES)" in source
    assert "data = response.read()" not in source


def test_public_pages_preserve_audience_and_nte_information_architecture(built_public_site):
    built, manifest = built_public_site
    home = (built / "index.html").read_text(encoding="utf-8")
    games = (built / "games" / "index.html").read_text(encoding="utf-8")
    operator = (built / "operator" / "index.html").read_text(encoding="utf-8")
    technology = (built / "technology" / "index.html").read_text(encoding="utf-8")
    app_script = (
        built / manifest["static_assets"]["/app.js"].lstrip("/")
    ).read_text(encoding="utf-8")

    assert "从一句指令，到一个群自己的日常" in home
    assert "当前版本 · 九项能力" in home
    assert "本群开关和全局条件必须同时满足" in home
    assert "#系统设置 被呼叫会话 状态" in home
    assert "#发言排行 周" in home
    assert "#nte薄荷排行" in home
    assert "Python 3.13" not in home
    assert "#nte薄荷排行" in games
    assert "#nte薄荷总排行" in games
    assert "#nte最强总排行" in games
    assert 'data-scope="total"' in games
    assert "机器人总排行" in app_script
    assert "群设置" in operator
    assert "Windows 本地运行" in technology
    assert "/api/*、/ws/*、/internal/*" in technology


def test_homepage_exploration_cards_use_dedicated_assets(built_public_site):
    built, manifest = built_public_site
    home = (built / "index.html").read_text(encoding="utf-8")

    for original_url in ("/assets/tangtang-avatar.jpg", "/assets/home-games.jpg"):
        built_url = manifest["static_assets"][original_url]
        assert built_url in home
        assert (built / built_url.lstrip("/")).is_file()


def test_mobile_marquee_groups_keep_their_intrinsic_width(built_public_site):
    built, manifest = built_public_site
    styles = (
        built / manifest["static_assets"]["/styles.css"].lstrip("/")
    ).read_text(encoding="utf-8")

    assert ".marquee-group { flex: 0 0 auto; width: max-content; min-width: max-content;" in styles
    assert ".marquee span { flex: 0 0 auto; }" in styles
