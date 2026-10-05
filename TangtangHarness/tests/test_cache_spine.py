"""Behavioral acceptance for cache-first context, not upstream hit simulation."""
from pathlib import Path
from dataclasses import replace
import base64
import io

import pytest
from PIL import Image

from scripts.verify_cache_spine import OfflineFixture, load_target, run_observation


@pytest.fixture(scope="module")
def observation(tmp_path_factory):
    return run_observation(Path(__file__).resolve().parents[1],
                           tmp_path_factory.mktemp("cache-spine-contract"))


def test_continuous_chat_keeps_every_prior_input_item(observation):
    stable = observation["stable"]
    assert stable["strict_append_pairs"] == stable["adjacent_pairs"] == 11
    assert stable["confirmed_turns"] == stable["rounds"]
    assert stable["sliding_rounds"] == 0


def test_changing_low_value_material_does_not_enter_every_turn(observation):
    # Five different data sources actually change during twelve model turns.
    # Their markers must not silently be duplicated into the canonical log.
    assert observation["stable"]["low_value_occurrences"] == 0


def test_rollover_is_a_complete_epoch_change_with_a_stable_recent_tail(observation):
    rollover = observation["rollover"]
    assert rollover["capacity_resets"] > 0
    assert rollover["same_epoch_pairs"] > 0
    assert rollover["same_epoch_strict_ratio"] == 1
    assert rollover["sliding_rounds"] == 0
    assert rollover["all_complete_pairs"]
    assert rollover["max_estimated_tokens"] <= rollover["budget"]
    assert rollover["confirmed_turns"] == rollover["rounds"]


def test_restarting_preserves_exact_next_input_and_preview_is_read_only(observation):
    for section in ("stable", "rollover"):
        assert observation[section]["restart_same_input"]
        assert observation[section]["preview_did_not_mutate_settings"]


def test_forgetting_and_filtering_override_prefix_reuse(observation):
    visibility = observation["visibility"]
    assert visibility["forgotten_removed"]
    assert visibility["filtered_removed"]
    assert visibility["forget_reset_reason"] == "forgotten"
    assert visibility["filter_reset_reason"] == "visibility"
    assert visibility["originals_preserved"]


def test_silent_failure_and_cancellation_release_the_window(observation):
    lifecycle = observation["lifecycle"]
    for outcome in ("silent", "failed", "cancel"):
        assert lifecycle[outcome]["pending"] is False
        assert lifecycle[outcome]["confirmed_turns"] == 0
    assert lifecycle["silent"]["status"] == "silent"
    assert lifecycle["failed"]["status"] == "failed"
    assert lifecycle["cancel"]["outcome"] == "cancelled"


def test_configured_fallback_does_not_change_model_three(observation):
    assert observation["lifecycle"]["failed"]["models"] == ["synthetic-model-3"]


def test_old_background_switches_and_queued_work_do_not_call_models(observation):
    assert observation["lifecycle"]["background"]["mock_model_calls"] == 0


def test_local_prefix_metrics_never_fabricate_provider_hits(observation):
    assert observation["real_network_calls"] == 0
    assert observation["provider_cache_hit_ratio"] is None
    assert observation["lifecycle"]["silent"]["usage_cache_read"] is None


def test_runtime_keeps_raw_generation_distinct_from_platform_receipts(observation):
    delivery = observation["runtime_delivery"]
    assert all(row["raw_output_preserved"] for row in delivery.values())
    assert delivery["delivered"]["confirmed_messages"] == ["第一条合成答复", "第二条合成答复"]
    assert delivery["partial"]["confirmed_messages"] == ["第一条合成答复"]
    for outcome in ("failed", "cancelled", "disabled"):
        assert delivery[outcome]["confirmed_turns"] == 0
        assert delivery[outcome]["fake_peer_sent"] == 0
    for outcome in ("delivered", "partial", "failed", "cancelled", "disabled"):
        row = delivery[outcome]
        assert row["pending"] is False
        assert row["receipt_facts"][-1]["status"] == outcome
    assert delivery["partial"]["receipt_facts"][-1]["sent_messages"] == ["第一条合成答复"]


def test_receipt_collector_failure_cannot_duplicate_an_already_sent_answer(observation):
    delivery = observation["runtime_delivery"]
    text = delivery["collector_failed"]
    assert text["error"] == ""
    assert text["fake_peer_sent"] == text["collector_calls"] == 2
    assert text["confirmed_messages"] == ["第一条合成答复", "第二条合成答复"]
    assert text["receipt_facts"][-1]["status"] == "delivered"
    voice = delivery["voice_collector_failed"]
    assert voice["error"] == ""
    assert voice["fake_peer_sent"] == voice["collector_calls"] == 1
    assert voice["fake_peer_message_types"] == ["record"]
    assert voice["confirmed_turns"] == 1
    assert voice["receipt_facts"][-1]["status"] == "delivered"
    assert text["pending"] is voice["pending"] is False


def test_receipt_collector_cancellation_preserves_only_actual_receipts(observation):
    delivery = observation["runtime_delivery"]
    for outcome in ("collector_cancelled", "voice_collector_cancelled"):
        row = delivery[outcome]
        assert row["error"] == "CancelledError"
        assert row["fake_peer_sent"] == row["collector_calls"] == 1
        assert row["confirmed_turns"] == 1
        assert row["pending"] is False
        assert row["receipt_facts"][-1]["status"] == "partial"
        assert row["receipt_facts"][-1]["sent_count"] == 1
        assert row["receipt_facts"][-1]["sent_messages"] == row["confirmed_messages"]
    assert delivery["collector_cancelled"]["confirmed_messages"] == ["第一条合成答复"]
    assert delivery["voice_collector_cancelled"]["fake_peer_message_types"] == ["record"]


def test_media_is_frozen_and_voice_changes_append_after_it(tmp_path):
    from tangtang_harness.log_context import persist_cache_context, append_cache_response

    api = load_target(Path(__file__).resolve().parents[1])
    fixture = OfflineFixture(api, tmp_path)
    profile = replace(fixture.config.profile(), vision=True, api_style="responses")
    config = replace(fixture.config, profiles=(profile,))

    def image(color):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 2), color).save(buffer, format="PNG")
        return {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}}

    first = fixture.event(1, text="这张合成图片是什么颜色？")
    fixture.store.append_event(first)
    prepared = api["context"].build_context(config, fixture.store, first, profile, images=[image("red")])
    persist_cache_context(fixture.store, prepared)
    append_cache_response(fixture.store, prepared, '{"decision":"reply","messages":["红色。"]}')
    retried = api["context"].build_context(config, fixture.store, first, profile, images=[image("blue")])
    assert retried.user_content == prepared.user_content
    assert retried.messages[:len(prepared.messages)] == prepared.messages
    next_context = api["context"].build_context(replace(config, speech_enabled=True), fixture.store,
        fixture.event(2, text="这次可以使用语音"), profile)
    assert next_context.messages[:len(retried.messages)] == retried.messages
    assert "语音可用" in next_context.user_content
    assert next_context.payload["input"][1]["content"][-1]["type"] == "input_image"


def test_oversized_current_message_fails_whole_without_committing_partial_state(tmp_path):
    api = load_target(Path(__file__).resolve().parents[1])
    fixture = OfflineFixture(api, tmp_path, budget=1_000)
    event = fixture.event(1, text="完整消息不可截断。" * 1_000)
    fixture.store.append_event(event)
    original = fixture.store.settings()
    with pytest.raises(api["context"].ContextBudgetError, match="本轮输入未被裁剪"):
        api["context"].build_context(fixture.config, fixture.store, event, fixture.config.profile())
    assert fixture.store.settings() == original
    assert fixture.store.requests() == []
