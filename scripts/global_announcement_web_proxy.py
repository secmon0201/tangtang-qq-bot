"""Expose only the global-announcement web paths to a Cloudflare tunnel."""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tangtang_web_gateway import route_public_target


LEGACY_PREFIX = "/announcement/"
HOP_BY_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
COPY_CHUNK_BYTES = 64 * 1024
UPSTREAM_TIMEOUT_SECONDS = 180


class AnnouncementProxy(BaseHTTPRequestHandler):
    server_version = "announcement-web-proxy/1.0"

    def _forward(self) -> None:
        upstream_target = self.path
        if not self.path.startswith(LEGACY_PREFIX):
            route = route_public_target(self.path)
            if route is None or route.name != "notice":
                self.send_error(404)
                return
            upstream_target = route.upstream_target
        length = int(self.headers.get("Content-Length", "0"))
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.target_port, timeout=UPSTREAM_TIMEOUT_SECONDS
        )
        headers = {key: value for key, value in self.headers.items() if key.lower() not in HOP_BY_HOP_HEADERS | {"host"}}
        try:
            connection.putrequest(self.command, upstream_target, skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{self.server.target_port}")
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
            self.send_error(503, "Announcement service unavailable")
            return
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in HOP_BY_HOP_HEADERS | {"content-length"}:
                self.send_header(key, value)
        response_length = response.getheader("Content-Length")
        if response_length is not None:
            self.send_header("Content-Length", response_length)
        self.end_headers()
        try:
            while chunk := response.read(COPY_CHUNK_BYTES):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()

    do_GET = _forward
    do_POST = _forward

    def log_message(self, format: str, *args) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--target-port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AnnouncementProxy)
    server.target_port = args.target_port
    print(
        f"announcement web proxy listening on http://127.0.0.1:{args.port} "
        "-> /notice/* only (legacy /announcement/* accepted)",
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
