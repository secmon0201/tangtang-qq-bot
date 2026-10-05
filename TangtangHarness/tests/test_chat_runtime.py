# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx

from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.models import ModelClient
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def fixtures(tmp_path, mode="live", handler=None):
    profile = ModelProfile("test", "Test", "custom", "synthetic", "https://example.invalid/v1")
    config = HarnessConfig(root=tmp_path, mode=mode, profiles=(profile,), active_model="test", extra={'context_mode': 'legacy'})
    store = Store(tmp_path)
    event = InboundEvent("1", 999, 101, 201, "合成问题", sender={"nickname": "合成"})
    def default(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"decision":"reply","messages":["合成答复"]}'}}],
                                        "usage": {"prompt_tokens": 100, "completion_tokens": 10}})
    client = ModelClient(transport=httpx.MockTransport(handler or default))
    return config, store, event, ChatService(config, store, client)


def test_observe_and_replay_never_call_models_or_confirm_history(tmp_path):
    for mode in ("observe", "replay"):
        def forbidden(request):
            raise AssertionError("离线模式不能访问模型")
        config, store, event, service = fixtures(tmp_path / mode, mode, forbidden)
        response = asyncio.run(service.respond(event))
        assert response.status == "observed"
        assert store.history(event.session_key) == []
        assert service.preview(event)["network"] is False


def test_generation_requires_external_delivery_confirmation(tmp_path):
    config, store, event, service = fixtures(tmp_path)
    result = asyncio.run(service.respond(event))
    assert result.messages == ["合成答复"]
    assert not store.history(event.session_key)
    store.confirm_response(result, event, ["42"])
    assert store.history(event.session_key)[0]["messages"] == result.messages
    assert store.request(result.request_id)["payload"] == result.payload
    assert result.usage["cache_read_tokens"] is None


def test_failed_actual_attempt_is_recorded_and_explicit_fallback_is_named(tmp_path):
    calls = []
    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload["model"])
        if payload["model"] == "primary":
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": '[接话][消息]备用答复'}}]})
    config, store, event, service = fixtures(tmp_path, handler=handler)
    first = replace(config.profiles[0], model="primary", fallback_profile_id="backup")
    backup = replace(first, id="backup", model="backup", fallback_profile_id="")
    service.config = replace(config, profiles=(first, backup))
    response = asyncio.run(service.respond(event))
    assert response.profile_id == "backup"
    assert calls == ["primary", "backup"]
    assert {row["model"] for row in store.requests()} == {"primary", "backup"}


def test_background_memory_saves_only_evidence_supported_fact(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts":[{"content":"喜欢画画","quote":"我喜欢画画","kind":"fact"}]}'}}]})
    config, store, event, service = fixtures(tmp_path, handler=handler)
    event = replace(event, text="我喜欢画画")
    service.enqueue_memory(event)
    result = asyncio.run(service.run_background_once())
    assert result["status"] == "completed"
    assert store.memories(event.session_key, event.user_id)[0]["content"] == "喜欢画画"
    assert store.requests()[0]["purpose"] == "memory"


def test_background_failure_does_not_publish_snapshot(tmp_path):
    config, store, event, service = fixtures(tmp_path)
    store.enqueue_job("compaction", event.session_key, {"cutoff_turn_id": 1, "turns": []})
    result = asyncio.run(service.run_background_once())
    assert result["status"] == "failed"
    assert store.snapshot(event.session_key) is None


def test_cost_projection_recommends_compaction_before_expensive_cache_tail(tmp_path):
    config, store, event, service = fixtures(tmp_path)
    profile = replace(config.profiles[0], input_price_per_million=1,
                      cache_read_price_per_million=10,
                      cache_write_price_per_million=1,
                      output_price_per_million=1)
    service.config = replace(config, profiles=(profile,), active_model=profile.id,
                             extra=dict({**config.extra, 'window_rebuild_history_ratio': 0.25,
                                    'window_cost_projection_rounds': 3}, context_mode='legacy'))
    request_id = store.add_request(event, profile, {'messages': []}, telemetry={'context_epoch': 1})
    store.finish_request(request_id, usage={
        'input_tokens': 1000, 'cache_read_tokens': 900, 'cache_write_tokens': 0,
        'cache_miss_tokens': 100, 'output_tokens': 10,
    })
    context = SimpleNamespace(telemetry={'estimated_input_tokens': 900, 'input_budget_tokens': 1000})
    decision = service._window_decision(context, session_key=event.session_key, profile=profile)
    assert decision['recommendation'] == 'switch_recommended'
    assert decision['estimate'] is True
