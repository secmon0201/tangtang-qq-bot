"""Edge contracts of the append-only model-input log, with no external IO."""
import json
from dataclasses import replace

import pytest

from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.log_context import (append_cache_delivery, append_cache_response,
                                         persist_cache_context)
from tangtang_harness.store import Store
from tangtang_harness.types import ChatResponse, InboundEvent
from tangtang_harness.context import message_tokens


def fixture(tmp_path):
    profile = ModelProfile('model-3', 'Synthetic 3', 'custom', 'synthetic-model',
                           'https://example.invalid/v1', context_limit=200_000,
                           max_output_tokens=512, vision=False)
    return HarnessConfig(root=tmp_path, profiles=(profile,), active_model=profile.id), Store(tmp_path), profile


def event(number, text='合成输入'):
    return InboundEvent(str(number), 999, 101, 201, text, timestamp=1_700_000_000 + number)


def test_retry_of_an_earlier_event_binds_output_to_that_event(tmp_path):
    config, store, profile = fixture(tmp_path)
    first, later = event(1), event(2)
    for incoming in (first, later):
        store.append_event(incoming)
        prepared = build_context(config, store, incoming, profile)
        persist_cache_context(store, prepared)
    retried = build_context(config, store, first, profile)
    persist_cache_context(store, retried)
    append_cache_response(store, retried, '{"decision":"reply","messages":["较早请求的生成"]}')
    state = store.get_setting(retried.telemetry['cache_state_key'])
    assert state['items'][-1]['event_key'] == first.key
    assert state['items'][-1]['source_events'] == [first.key]


def test_one_complete_current_input_can_use_budget_without_false_rollover(tmp_path):
    config, store, profile = fixture(tmp_path)
    incoming = event(1, '较长的当前输入。' * 500)
    store.append_event(incoming)
    large = build_context(config, store, incoming, profile, input_budget_tokens=20_000)
    budget = large.telemetry['estimated_input_tokens'] + 16
    first = build_context(config, store, incoming, profile, input_budget_tokens=budget)
    assert first.telemetry['estimated_input_tokens'] > first.telemetry['hard_watermark']
    assert first.telemetry['cache_spine_epoch'] == 1
    assert first.telemetry['cache_spine_reset_reason'] == ''
    persist_cache_context(store, first)
    retried = build_context(config, store, incoming, profile, input_budget_tokens=budget)
    assert retried.telemetry['cache_spine_epoch'] == 1
    assert retried.payload == first.payload


def test_bounded_new_event_backlog_records_every_omission(tmp_path):
    config, store, profile = fixture(tmp_path)
    all_events = [event(number, '新收到资料 ' + str(number)) for number in range(1, 42)]
    for incoming in all_events:
        store.append_event(incoming)
    prepared = build_context(config, store, all_events[-1], profile)
    assert prepared.telemetry['omitted_backlog_event_keys'] == [item.key for item in all_events[:11]]
    adopted = set(prepared.cache_state['items'][-1]['source_events'])
    omitted = set(prepared.telemetry['omitted_backlog_event_keys'])
    assert adopted.isdisjoint(omitted)
    assert adopted | omitted == {item.key for item in all_events}
    persist_cache_context(store, prepared)
    following = event(42)
    store.append_event(following)
    next_context = build_context(config, store, following, profile)
    assert next_context.telemetry['omitted_backlog_event_keys'] == []
    assert next_context.messages[:len(prepared.messages)] == prepared.messages


def test_model_output_and_partial_delivery_are_two_append_only_facts(tmp_path):
    config, store, profile = fixture(tmp_path)
    incoming = event(1)
    store.append_event(incoming)
    prepared = build_context(config, store, incoming, profile)
    request_id = store.add_request(incoming, profile, prepared.payload, telemetry=prepared.telemetry)
    persist_cache_context(store, prepared)
    raw = '{"decision":"reply","messages":["完整生成一","完整生成二"]}'
    append_cache_response(store, prepared, raw)
    response = ChatResponse(request_id, incoming.session_key, ['完整生成一', '完整生成二'], {},
                            profile.id, prepared.payload, user_content=prepared.user_content)
    append_cache_delivery(store, response, incoming, ['实际送达一'], status='partial')
    state = store.get_setting(prepared.telemetry['cache_state_key'])
    assert state['items'][-2]['role'] == 'assistant'
    assert state['items'][-2]['content'] == raw
    fact = json.loads(state['items'][-1]['content'].split('\n', 1)[1])
    assert fact['status'] == 'partial'
    assert fact['sent_messages'] == ['实际送达一']
    assert store.history(incoming.session_key) == []
    next_context = build_context(config, store, event(2), profile)
    assert next_context.messages[:len(prepared.messages)] == prepared.messages
    assert raw in [item['content'] for item in next_context.messages]


def test_full_receipt_only_repeats_text_when_actual_delivery_differs(tmp_path):
    config, store, profile = fixture(tmp_path)
    for number, sent in ((1, ['原样送达']), (2, ['实际截断'])):
        incoming = event(number)
        store.append_event(incoming)
        prepared = build_context(config, store, incoming, profile)
        request_id = store.add_request(incoming, profile, prepared.payload, telemetry=prepared.telemetry)
        persist_cache_context(store, prepared)
        raw = json.dumps({'decision': 'reply', 'messages': ['原样送达']}, ensure_ascii=False)
        append_cache_response(store, prepared, raw)
        response = ChatResponse(request_id, incoming.session_key, sent, {}, profile.id,
                                prepared.payload, user_content=prepared.user_content)
        append_cache_delivery(store, response, incoming, sent)
        state = store.get_setting(prepared.telemetry['cache_state_key'])
        fact = json.loads(state['items'][-1]['content'].split('\n', 1)[1])
        assert state['items'][-2]['content'] == raw
        if number == 1:
            assert 'sent_messages' not in fact
        else:
            assert fact['sent_messages'] == ['实际截断']


@pytest.mark.parametrize('revocation', ['filter', 'forget', 'forget_multiline'])
def test_revocation_clears_cross_round_paraphrases_without_replaying_backlog(tmp_path, revocation):
    config, store, profile = fixture(tmp_path)
    original = 'SYNTHETIC_PRIVATE_ADDRESS'
    if revocation == 'forget_multiline':
        original += '\nSYNTHETIC_SECOND_LINE'
    first = replace(event(1, '本人旧资料：' + original), user_id=102)
    later = event(2, '追问此前成员的旧资料')
    paraphrase = '那位成员住在合成北岸区域。'
    store.remember(first.session_key, first.user_id, original, event_key=first.key, quote=original)
    for incoming, answer in ((first, '已生成记录'), (later, paraphrase)):
        store.append_event(incoming)
        prepared = build_context(config, store, incoming, profile)
        request_id = store.add_request(incoming, profile, prepared.payload, telemetry=prepared.telemetry)
        persist_cache_context(store, prepared)
        raw = json.dumps({'decision': 'reply', 'messages': [answer]}, ensure_ascii=False)
        append_cache_response(store, prepared, raw)
        store.confirm_turn(incoming, [answer], request_id, ['receipt-' + incoming.event_id], prepared.user_content)
    if revocation == 'filter':
        from tangtang_harness.business.db import Database
        business = Database(tmp_path / 'runtime' / 'business.db')
        business.seed_groups((201,))
        business.add_filter_members('active', [first.user_id], 101)
    else:
        store.forget(first.session_key, first.user_id, original)
    current = event(3, '继续聊音乐')
    store.append_event(current)
    rebuilt = build_context(config, store, current, profile, tool_facts={'current': '新工具事实'})
    encoded = json.dumps(rebuilt.payload, ensure_ascii=False)
    assert original.split('\n')[0] not in encoded
    assert paraphrase not in encoded
    assert later.text not in encoded
    assert '新工具事实' in encoded
    assert len(rebuilt.cache_state['items']) == 1
    assert rebuilt.cache_state['items'][0]['source_events'] == [current.key]
    assert rebuilt.telemetry['cache_spine_epoch'] == 2
    assert rebuilt.telemetry['cache_spine_reset_reason'] == ('visibility' if revocation == 'filter' else 'forgotten')
    assert set(rebuilt.telemetry['epoch_removed_event_keys']) == {first.key, later.key}
    assert len(store.history(first.session_key)) == 2
    assert store.event(first.key)['payload']['text'] == first.text


@pytest.mark.parametrize('revocation', ['filter', 'forget', 'scope'])
def test_policy_checkpoint_revokes_paraphrases_after_source_was_dropped_for_capacity(tmp_path, revocation):
    config, store, profile = fixture(tmp_path)
    config = replace(config, recent_rounds=1)
    marker = 'SYNTHETIC_ORIGINAL_SOURCE'
    first = replace(event(1, marker + '较长的原始资料。' * 800), user_id=102)
    later = event(2, '后续追问原始资料')
    paraphrase = '被改写后的合成敏感事实。'
    store.remember(first.session_key, first.user_id, marker, event_key=first.key, quote=marker)
    for incoming, answer in ((first, '收到资料'), (later, paraphrase)):
        store.append_event(incoming)
        store.set_setting('event_scope:' + incoming.key, {'chat_allowed': True, 'kind': 'chat'})
        prepared = build_context(config, store, incoming, profile)
        request_id = store.add_request(incoming, profile, prepared.payload, telemetry=prepared.telemetry)
        persist_cache_context(store, prepared)
        append_cache_response(store, prepared, json.dumps({'messages': [answer]}, ensure_ascii=False))
        store.confirm_turn(incoming, [answer], request_id, ['receipt-' + incoming.event_id], prepared.user_content)
    third = event(3, '正常的新一轮')
    store.append_event(third)
    preview = build_context(config, store, third, profile)
    smaller_budget = int(message_tokens(preview.messages, profile) * 1.02)
    rolled = build_context(config, store, third, profile, input_budget_tokens=smaller_budget)
    assert rolled.telemetry['cache_spine_reset_reason'] == 'capacity'
    assert first.key in rolled.telemetry['epoch_removed_event_keys']
    assert marker not in json.dumps(rolled.payload, ensure_ascii=False)
    assert paraphrase in json.dumps(rolled.payload, ensure_ascii=False)
    persist_cache_context(store, rolled)
    if revocation == 'filter':
        from tangtang_harness.business.db import Database
        business = Database(tmp_path / 'runtime' / 'business.db')
        business.seed_groups((201,))
        business.add_filter_members('active', [first.user_id], 101)
    elif revocation == 'forget':
        store.forget(first.session_key, first.user_id, marker)
    else:
        store.set_setting('event_scope:' + first.key, {'chat_allowed': False, 'kind': 'revoked'})
    current = event(4, '切换到新的合成话题')
    store.append_event(current)
    rebuilt = build_context(config, store, current, profile)
    assert paraphrase not in json.dumps(rebuilt.payload, ensure_ascii=False)
    assert len(rebuilt.cache_state['items']) == 1
    assert rebuilt.cache_state['items'][0]['source_events'] == [current.key]
    assert rebuilt.telemetry['cache_spine_epoch'] == rolled.telemetry['cache_spine_epoch'] + 1
    assert rebuilt.telemetry['cache_spine_reset_reason'] == ('forgotten' if revocation == 'forget' else 'visibility')
    assert len(store.history(current.session_key)) == 2


def test_scope_checkpoint_changes_only_for_an_existing_permission_revocation(tmp_path):
    _, store, _ = fixture(tmp_path)
    incoming = event(1)
    store.append_event(incoming)
    key, revision_key = 'event_scope:' + incoming.key, 'cache_scope_revision:' + incoming.session_key
    for kind in ('observed', 'tool'):
        store.set_setting(key, {'chat_allowed': False, 'kind': kind})
        assert store.get_setting(revision_key, 0) == 0
    store.set_setting(key, {'chat_allowed': True, 'kind': 'chat'})
    assert store.get_setting(revision_key, 0) == 0
    store.set_setting(key, {'chat_allowed': False, 'kind': 'revoked'})
    assert store.get_setting(revision_key) == 1
    store.set_setting(key, {'chat_allowed': False, 'kind': 'revoked'})
    store.set_setting(key, {'chat_allowed': True, 'kind': 'restored'})
    assert store.get_setting(revision_key) == 1
