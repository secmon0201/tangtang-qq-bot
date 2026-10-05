"""Capacity/usage contracts with synthetic profiles and no external IO."""
import asyncio
import base64
import io
from copy import deepcopy
from dataclasses import replace

import httpx
import pytest
from PIL import Image

from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import ContextBudgetError, build_context, message_tokens
from tangtang_harness.experiments import run_experiment
from tangtang_harness.log_context import (append_cache_delivery_by_request, append_cache_response,
                                         observe_cache_usage, persist_cache_context)
from tangtang_harness.models import ModelClient, ModelResult, normalize_usage
from tangtang_harness.runtime import Runtime
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def fixture(tmp_path, *, vision=False):
    profile = ModelProfile('model-3', 'Synthetic', 'custom', 'synthetic-model',
                           'https://example.invalid/v1', context_limit=1_048_574,
                           max_output_tokens=16_000, vision=vision)
    return HarnessConfig(root=tmp_path, profiles=(profile,), active_model=profile.id), Store(tmp_path), profile


def event(number, text='完整的合成输入'):
    return InboundEvent(str(number), 999, 101, 201, text, timestamp=1_700_000_000 + number)


def seeded(tmp_path, count=45):
    config, store, profile = fixture(tmp_path)
    for number in range(1, count + 1):
        incoming = event(number)
        store.append_event(incoming)
        context = build_context(config, store, incoming, profile)
        persist_cache_context(store, context)
        append_cache_response(store, context, '{"decision":"reply","messages":["合成答复"]}')
        request = store.add_request(incoming, profile, context.payload, telemetry=context.telemetry)
        append_cache_delivery_by_request(store, request, incoming, ['合成答复'])
    incoming = event(count + 1)
    store.append_event(incoming)
    return config, store, profile, incoming, build_context(config, store, incoming, profile)


def calibration_rows(store):
    with store.connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT key,value FROM settings WHERE key LIKE 'cache_token_calibration:%' ORDER BY key")]


def test_default_budget_uses_configured_model_capacity_and_marks_provenance(tmp_path):
    config, store, profile = fixture(tmp_path)
    context = build_context(config, store, event(1), profile)
    telemetry = context.telemetry
    assert config.cache_input_budget_tokens is None
    assert telemetry['input_budget_tokens'] == 1_031_550
    assert telemetry['hard_watermark'] == 928_395
    assert telemetry['cache_retention_target_tokens'] == 515_775
    assert telemetry['cache_budget_source'] == 'model_capacity'
    assert telemetry['cache_capacity_source'] == 'profile_configuration'
    assert telemetry['cache_capacity_verification'] == 'not_verified_by_harness'
    assert telemetry['calibrated_input_tokens'] is None
    assert telemetry['capacity_estimated_input_tokens'] == telemetry['local_input_tokens']


def test_rollover_retains_more_than_eight_complete_continuous_groups_in_one_pass(tmp_path, monkeypatch):
    config, store, profile, incoming, full = seeded(tmp_path)
    from tangtang_harness import log_context
    counted = []
    original = log_context._item_cost

    def count(content, selected):
        counted.append(content)
        return original(content, selected)

    monkeypatch.setattr(log_context, '_item_cost', count)
    budget = int(full.telemetry['local_input_tokens'] * .8)
    rolled = build_context(replace(config, recent_rounds=1), store, incoming, profile,
                           input_budget_tokens=budget)
    assert len(counted) == len(full.cache_state['items'])
    retained = rolled.telemetry['cache_retained_event_keys']
    assert len(retained) > 9
    assert retained == [event(number).key for number in range(47 - len(retained), 47)]
    for key in retained[:-1]:
        assert [item['kind'] for item in rolled.cache_state['items'] if item['event_key'] == key] == [
            'input', 'generated', 'delivery']
    assert rolled.telemetry['cache_retention_ratio'] == .5
    assert rolled.telemetry['capacity_estimated_input_tokens'] <= int(budget * .5)
    assert rolled.telemetry['local_input_tokens'] == message_tokens(rolled.messages, profile)
    assert rolled.telemetry['cache_spine_reset_reason'] == 'capacity'
    assert rolled.messages[0] == full.messages[0]
    persist_cache_context(store, rolled)
    next_context = build_context(config, store, event(47), profile)
    assert next_context.telemetry['cache_spine_epoch'] == rolled.telemetry['cache_spine_epoch']
    assert next_context.messages[:len(rolled.messages)] == rolled.messages


def test_large_newer_group_is_not_skipped_to_keep_an_older_small_group(tmp_path):
    config, store, profile = fixture(tmp_path)
    for incoming in (event(1), event(2, '较大的最新完整事件组' * 400)):
        store.append_event(incoming)
        context = build_context(config, store, incoming, profile)
        persist_cache_context(store, context)
    incoming = event(3)
    full = build_context(config, store, incoming, profile)
    rolled = build_context(config, store, incoming, profile,
                           input_budget_tokens=int(full.telemetry['local_input_tokens'] * .95))
    assert rolled.telemetry['cache_retained_event_keys'] == [incoming.key]
    assert len(rolled.cache_state['items']) == 1


def test_retry_preserves_complete_current_group_and_overflow_does_not_write(tmp_path):
    config, store, profile, _, _ = seeded(tmp_path, count=2)
    retried = build_context(config, store, event(1), profile)
    current = [item for item in retried.cache_state['items'] if item['event_key'] == event(1).key]
    floor = message_tokens([retried.messages[0], *({'role': item['role'], 'content': item['content']}
                                                 for item in current)], profile)
    key = retried.telemetry['cache_state_key']
    previous = store.get_setting(key)
    with pytest.raises(ContextBudgetError, match='本轮输入未被裁剪'):
        build_context(config, store, event(1), profile, input_budget_tokens=floor - 1)
    assert store.get_setting(key) == previous
    rolled = build_context(config, store, event(1), profile, input_budget_tokens=floor + 8)
    assert rolled.cache_state['items'] == current
    assert [item['kind'] for item in current] == ['input', 'generated', 'delivery']


@pytest.mark.parametrize('actual', [None, 0, -1, True, 1.5])
def test_missing_or_invalid_usage_does_not_write_calibration(tmp_path, actual):
    config, store, profile = fixture(tmp_path)
    context = build_context(config, store, event(1), profile)
    assert observe_cache_usage(store, profile, context, {'input_tokens': actual}) == 'missing_input_usage'
    assert calibration_rows(store) == []


def test_normalized_usage_is_not_added_to_cached_input_and_payload_retry_is_deduplicated(tmp_path):
    config, store, profile = fixture(tmp_path)
    context = build_context(config, store, event(1), profile)
    local = context.telemetry['local_input_tokens']
    usage = normalize_usage({'usage': {'prompt_tokens': local * 2, 'completion_tokens': 10,
                                      'prompt_tokens_details': {'cached_tokens': local}}}, profile)
    original_payload, original_usage = deepcopy(context.payload), deepcopy(usage)
    assert observe_cache_usage(store, profile, context, usage) == 'recorded'
    assert observe_cache_usage(store, profile, context, usage) == 'duplicate_payload'
    before = calibration_rows(store)
    preview = build_context(config, store, event(2), profile)
    assert preview.telemetry['calibrated_input_tokens'] == preview.telemetry['local_input_tokens'] * 2
    assert preview.telemetry['capacity_token_count_source'] == 'usage_calibrated_local_estimate'
    assert calibration_rows(store) == before
    assert context.payload == original_payload
    assert usage == original_usage
    assert usage['cache_read_tokens'] == local
    assert usage['input_tokens'] == local * 2


def test_calibration_has_bounded_samples_median_and_absolute_relative_outlier_limits(tmp_path):
    config, store, profile = fixture(tmp_path)
    for number in range(1, 13):
        context = build_context(config, store, event(number), profile)
        assert observe_cache_usage(store, profile, context, {
            'input_tokens': context.telemetry['local_input_tokens'] * 2}) == 'recorded'
    context = build_context(config, store, event(13), profile)
    local = context.telemetry['local_input_tokens']
    assert context.telemetry['capacity_calibration_samples'] == 9
    assert context.telemetry['calibrated_input_tokens'] == local * 2
    original = calibration_rows(store)
    assert observe_cache_usage(store, profile, context, {'input_tokens': local * 10}) == 'outlier'
    assert observe_cache_usage(store, profile, context, {'input_tokens': int(local * .5)}) == 'outlier'
    assert calibration_rows(store) == original


@pytest.mark.parametrize('ratio', [.5, 1.5])
def test_true_usage_corrects_both_overestimate_and_underestimate_without_editing_prefix(tmp_path, ratio):
    config, store, profile, incoming, full = seeded(tmp_path)
    local = full.telemetry['local_input_tokens']
    budget = int(local * (.95 if ratio == .5 else 1.2))
    before = build_context(config, store, incoming, profile, input_budget_tokens=budget)
    assert (before.telemetry['cache_spine_reset_reason'] == 'capacity') is (ratio == .5)
    payload = deepcopy(full.payload)
    previous = store.get_setting(full.telemetry['cache_state_key'])
    assert observe_cache_usage(store, profile, full, {'input_tokens': int(local * ratio)}) == 'recorded'
    after = build_context(config, store, incoming, profile, input_budget_tokens=budget)
    assert (after.telemetry['cache_spine_reset_reason'] == 'capacity') is (ratio == 1.5)
    assert after.messages[0] == full.messages[0]
    if ratio == .5:
        assert after.payload == full.payload
    assert full.payload == payload
    assert store.get_setting(full.telemetry['cache_state_key']) == previous


def test_tokenizer_channel_and_media_changes_do_not_transfer_unrelated_samples(tmp_path):
    config, store, profile = fixture(tmp_path, vision=True)
    first = build_context(config, store, event(1), profile)
    persist_cache_context(store, first)
    observe_cache_usage(store, profile, first, {'input_tokens': first.telemetry['local_input_tokens'] * 2})
    for selected in (replace(profile, tokenizer='synthetic-no-such-tokenizer'),
                     replace(profile, base_url='https://alternate.example.invalid/v1')):
        view = build_context(config, store, event(2), selected)
        assert view.telemetry['calibrated_input_tokens'] is None
        if selected.tokenizer:
            assert view.telemetry['cache_state_key'] == first.telemetry['cache_state_key']
            assert view.messages[:len(first.messages)] == first.messages
    buffer = io.BytesIO()
    Image.new('RGB', (1024, 1024)).save(buffer, format='PNG')
    image = {'type': 'image_url', 'image_url': {
        'url': 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')}}
    with_image = build_context(config, store, event(2), profile, images=[image])
    assert with_image.telemetry['calibrated_input_tokens'] is None
    assert observe_cache_usage(store, profile, with_image, {
        'input_tokens': with_image.telemetry['local_input_tokens'] * 2}) == 'recorded'
    changed_share = build_context(config, store, event(2, '图片后的较长文本' * 400), profile, images=[image])
    assert changed_share.telemetry['calibrated_input_tokens'] is None
    assert first.payload['messages'][0] == changed_share.payload['messages'][0]


def test_successful_generation_usage_is_observed_even_if_business_parse_fails(tmp_path, monkeypatch):
    config, store, profile = fixture(tmp_path)
    config = replace(config, mode='live')

    def handler(request):
        import json
        local = message_tokens(json.loads(request.content)['messages'], profile)
        return httpx.Response(200, json={'choices': [{'message': {'content': '生成成功'}}],
                                        'usage': {'prompt_tokens': local * 2, 'completion_tokens': 10}})

    def invalid_business_reply(*args, **kwargs):
        raise ValueError('synthetic business parse error')

    monkeypatch.setattr('tangtang_harness.chat.parse_reply', invalid_business_reply)
    client = ModelClient(transport=httpx.MockTransport(handler))
    service = ChatService(config, store, client)
    response = asyncio.run(service.respond(event(1)))
    assert response.status == 'failed'
    assert len(calibration_rows(store)) == 1
    assert response.usage['input_tokens'] > 0
    assert response.usage['cache_read_tokens'] is None
    recorded = store.request(response.request_id)['usage']
    assert recorded['input_tokens'] == response.usage['input_tokens']
    assert recorded['raw'] == response.usage['raw']
    assert recorded['cache_read_tokens'] is None


@pytest.mark.parametrize('kind', ['cold_warm', 'prefix_change'])
def test_paid_experiment_only_records_actual_usage_and_exact_payload_estimate(tmp_path, kind):
    config, _, profile = fixture(tmp_path)

    class Model:
        async def generate(self, selected, payload):
            local = message_tokens(payload['messages'], selected)
            return ModelResult('模拟生成', {'input_tokens': local * 2, 'cache_read_tokens': None})

        async def close(self):
            pass

    runtime = Runtime(replace(config, mode='live'), model_client=Model())
    try:
        result = asyncio.run(run_experiment(runtime, {'kind': kind, 'paid': True}))
        assert result['model_calls'] == 2 and result['qq_writes'] == 0
        assert result['result']['actual_cache'] == [None, None]
        view = build_context(config, runtime.store, event(3), profile)
        assert view.telemetry['capacity_calibration_samples'] == (1 if kind == 'cold_warm' else 2)
        assert view.telemetry['calibrated_input_tokens'] == view.telemetry['local_input_tokens'] * 2
    finally:
        asyncio.run(runtime.close())
