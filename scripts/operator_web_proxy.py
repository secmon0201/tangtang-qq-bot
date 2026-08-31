"""Expose only capability-scoped operator web paths to a Cloudflare tunnel."""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tangtang_web_gateway import OPERATOR_KINDS, route_public_target


LEGACY_PREFIX = "/operator/"
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


class OperatorProxy(BaseHTTPRequestHandler):
    server_version = "operator-web-proxy/1.0"

    def _forward(self) -> None:
        upstream_target = self.path
        if not self.path.startswith(LEGACY_PREFIX):
            route = route_public_target(self.path)
            if route is None or route.name not in OPERATOR_KINDS:
                self.send_error(404)
                return
            upstream_target = route.upstream_target
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        connection = http.client.HTTPConnection("127.0.0.1", self.server.target_port, timeout=60)
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_BY_HOP_HEADERS | {"host"}}
        try:
            connection.request(self.command, upstream_target, body=body, headers=headers)
            response = connection.getresponse(); data = response.read()
        except OSError:
            self.send_error(503, "Operator web service unavailable")
            return
        finally:
            connection.close()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP_HEADERS | {"content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    do_GET = _forward
    do_POST = _forward

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18767)
    parser.add_argument("--target-port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), OperatorProxy)
    server.target_port = args.target_port
    print(
        f"operator web proxy listening on http://127.0.0.1:{args.port} "
        "-> /activity/*, /operations/*, /duplicate/* only (legacy /operator/* accepted)",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
