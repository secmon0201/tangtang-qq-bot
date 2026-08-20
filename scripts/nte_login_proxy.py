"""Path-restricted reverse proxy for the NTE login pages.

cloudflared quick tunnels cannot filter paths, while GsUID Core also hosts
sensitive endpoints such as ``/ws/*`` and ``/api/*``. This stdlib-only proxy
listens on 127.0.0.1:18765 and forwards ONLY ``/nte/*`` to the local Core;
every other path returns 404. Point cloudflared at this proxy, not the Core.
"""

from __future__ import annotations

import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


TARGET = "http://127.0.0.1:8765"
ALLOWED_PREFIX = "/nte/"
FORWARD_REQUEST_HEADERS = (
    "Content-Type",
    "Accept",
    "Accept-Language",
    "Origin",
    "Referer",
    "User-Agent",
    "Cookie",
)


class NteProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "nte-login-proxy/1.0"

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else None
        headers: dict[str, str] = {}
        for name in FORWARD_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value:
                headers[name] = value
        if body is not None:
            headers["Content-Length"] = str(len(body))
        target = urlsplit(TARGET)
        try:
            connection = HTTPConnection(target.hostname, target.port or 80, timeout=60)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self._reply(response.status, response.getheaders(), payload)
            connection.close()
        except (OSError, TimeoutError):
            self._reply(502, (), b"origin unavailable")

    def _reply(self, status: int, headers, payload: bytes) -> None:
        self.send_response(status)
        seen_content_length = False
        for name, value in headers:
            lowered = name.lower()
            if lowered in {
                "content-type",
                "content-length",
                "location",
                "set-cookie",
                "cache-control",
            }:
                self.send_header(name, value)
                if lowered == "content-length":
                    seen_content_length = True
        if not seen_content_length:
            self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _reject(self) -> None:
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIX):
            self._reject()
            return
        self._forward()

    def do_POST(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIX):
            self._reject()
            return
        self._forward()

    def do_HEAD(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIX):
            self._reject()
            return
        self._forward()

    def log_message(self, fmt: str, *args: object) -> None:
        print(fmt % args, file=sys.stderr, flush=True)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 18765), NteProxyHandler)
    print("nte-login proxy listening on http://127.0.0.1:18765 -> /nte/* only", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
