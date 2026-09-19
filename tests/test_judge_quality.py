"""Semantic judge runner against a local mock endpoint."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bot.services.quality_eval import GoldenCase
from scripts.judge_skills_quality import _endpoint, judge, summarize


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        assert payload.get("messages"), "judge request must carry messages"
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "natural": 5,
                                    "factual": 4,
                                    "no_fabrication": 5,
                                    "persona": 4,
                                    "reason": "自然",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence test server
        return


def _server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def test_endpoint_normalization():
    assert _endpoint("http://127.0.0.1:3123/v1") == "http://127.0.0.1:3123/v1/chat/completions"
    assert _endpoint("http://127.0.0.1:3123/v1/chat/completions").endswith("/chat/completions")


def test_judge_scores_against_mock_endpoint():
    server, api_url = _server()
    try:
        cases = (
            GoldenCase("a", "chat", "你好", {}),
            GoldenCase("b", "chat", "再见", {}),
        )
        results = {"a": {"reply": "你好呀"}, "b": {"reply": "再见"}}
        scores = asyncio.run(
            judge(
                api_url=api_url,
                api_key="test",
                model="test-model",
                cases=cases,
                results=results,
                timeout=10.0,
            )
        )
        assert all(isinstance(row.get("judge"), dict) for row in scores)
        summary = summarize(scores)
        assert summary["judged"] == 2
        assert summary["errors"] == 0
        assert summary["scores"]["natural"] == 5.0
        assert summary["scores"]["factual"] == 4.0
    finally:
        server.shutdown()
        server.server_close()


def test_judge_skips_empty_replies():
    server, api_url = _server()
    try:
        cases = (GoldenCase("empty", "chat", "你好", {}),)
        scores = asyncio.run(
            judge(
                api_url=api_url,
                api_key="",
                model="test-model",
                cases=cases,
                results={"empty": {"reply": ""}},
                timeout=10.0,
            )
        )
        assert scores[0]["skipped"] == "empty_reply"
    finally:
        server.shutdown()
        server.server_close()
