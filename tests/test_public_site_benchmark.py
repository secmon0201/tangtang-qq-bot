from __future__ import annotations

import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_benchmark():
    path = ROOT / "scripts" / "benchmark_public_site.py"
    spec = importlib.util.spec_from_file_location("benchmark_public_site_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_percentile_interpolates_small_samples():
    benchmark = load_benchmark()

    assert benchmark._percentile([10.0, 20.0], 0.50) == 15.0
    assert benchmark._percentile([10.0, 20.0], 0.95) == 19.5


def test_benchmark_completes_exact_request_count_with_persistent_workers():
    benchmark = load_benchmark()
    connection_ids: set[int] = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            connection_ids.add(id(self.connection))
            body = self.path.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Encoding", "br")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        samples, errors, elapsed = benchmark.run_benchmark(
            f"http://127.0.0.1:{server.server_port}",
            ("/", "/games/"),
            request_count=12,
            concurrency=3,
            timeout=5,
            accept_encoding="br, gzip",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert errors == []
    assert len(samples) == 12
    assert {sample.status for sample in samples} == {200}
    assert {sample.encoding for sample in samples} == {"br"}
    assert 1 <= len(connection_ids) <= 3
    assert elapsed > 0
