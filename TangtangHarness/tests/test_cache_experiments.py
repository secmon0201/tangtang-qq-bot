"""Console experiments use the same append-only context as foreground chat."""
import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.experiments import run_experiment
from tangtang_harness.models import ModelResult
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent


class Model:
    def __init__(self):
        self.calls = []

    async def generate(self, profile, payload):
        self.calls.append(deepcopy(payload))
        return ModelResult('{"decision":"reply","messages":["真实模拟输出，不是离线种子"]}',
                           {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": None})


def runtime_for(tmp_path, api_style="chat_completions", mode="observe"):
    profile = ModelProfile("model-3", "Synthetic", "custom", "synthetic",
                           "https://example.invalid/v1", api_style=api_style)
    model = Model()
    return Runtime(HarnessConfig(root=tmp_path, mode=mode, profiles=(profile,),
                                 active_model=profile.id), model_client=model)


@pytest.mark.parametrize("api_style", ["chat_completions", "responses"])
def test_offline_append_uses_canonical_transcript_without_business_writes(tmp_path, api_style):
    runtime = runtime_for(tmp_path, api_style)
    try:
        result = asyncio.run(run_experiment(runtime, {"kind": "cache_append"}))
        before, after = [step["preview"]["payload"] for step in result["result"]["steps"]]
        key = "input" if api_style == "responses" else "messages"
        assert after[key][:len(before[key])] == before[key]
        assert after[key][len(before[key])]["role"] == "assistant"
        assert "history" in result["result"]["diff"]["changed_layers"]
        assert result["model_calls"] == result["qq_writes"] == 0
        assert result["result"]["actual_cache"] is None
        assert not runtime.chat.model_client.calls
        assert not runtime.store.requests()
        with runtime.store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'cache_context:%'").fetchone()[0] == 0
    finally:
        asyncio.run(runtime.close())


def test_paid_append_second_input_contains_actual_first_output(tmp_path):
    runtime = runtime_for(tmp_path, mode="live")
    try:
        result = asyncio.run(run_experiment(runtime, {"kind": "cache_append", "paid": True}))
        first, second = runtime.chat.model_client.calls
        assert second["messages"][:len(first["messages"])] == first["messages"]
        assert second["messages"][len(first["messages"])] == {
            "role": "assistant", "content": '{"decision":"reply","messages":["真实模拟输出，不是离线种子"]}'}
        assert result["model_calls"] == 2 and result["qq_writes"] == 0
        assert result["result"]["actual_cache"] == [None, None]
        with runtime.store.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
    finally:
        asyncio.run(runtime.close())


def test_retired_compaction_experiment_never_calls_model(tmp_path):
    runtime = runtime_for(tmp_path, mode="live")
    try:
        with pytest.raises(ValueError, match="停用 AI 压缩"):
            asyncio.run(run_experiment(runtime, {"kind": "compaction", "paid": True}))
        assert not runtime.chat.model_client.calls and not runtime.store.requests()
    finally:
        asyncio.run(runtime.close())


def test_replay_of_real_event_preserves_its_scope_and_active_transcript(tmp_path):
    runtime = runtime_for(tmp_path)
    event = InboundEvent("real", 103, 101, 102, "真实已采纳的合成输入", timestamp=1)
    runtime.store.append_event(event)
    scope = {"chat_allowed": True, "kind": "chat"}
    runtime.store.set_setting("event_scope:" + event.key, scope)
    from tangtang_harness.context import build_context
    from tangtang_harness.log_context import persist_cache_context
    context = build_context(runtime.config, runtime.store, event, runtime.config.profile())
    persist_cache_context(runtime.store, context)
    state = runtime.store.get_setting(context.telemetry["cache_state_key"])
    try:
        with TestClient(create_app(runtime=runtime)) as client:
            response = client.post("/api/replay", json={"event": event.to_dict()})
            assert response.status_code == 200
            assert response.json()["model_calls"] == response.json()["qq_writes"] == 0
            assert response.json()["replay_event_key"] == event.key
        assert runtime.store.get_setting("event_scope:" + event.key) == scope
        assert runtime.store.get_setting(context.telemetry["cache_state_key"]) == state
        assert not runtime.chat.model_client.calls and not runtime.store.requests()
    finally:
        asyncio.run(runtime.close())


def test_paid_experiment_cancellation_finishes_attempt_without_second_call(tmp_path):
    from unittest.mock import AsyncMock
    runtime = runtime_for(tmp_path, mode="live")
    runtime.chat.model_client.generate = AsyncMock(side_effect=asyncio.CancelledError)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_experiment(runtime, {"kind": "cache_append", "paid": True}))
        requests = runtime.store.requests()
        assert len(requests) == 1 and requests[0]["outcome"] == "cancelled"
        assert requests[0]["usage"].get("cache_read_tokens") is None
        assert runtime.chat.model_client.generate.await_count == 1
    finally:
        asyncio.run(runtime.close())
