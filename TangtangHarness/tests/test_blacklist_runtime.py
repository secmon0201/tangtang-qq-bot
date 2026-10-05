# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import asyncio
import base64
import io
import sqlite3
from dataclasses import replace

from PIL import Image

from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.media import MediaResolver
from tangtang_harness.store import Store, encode
from tangtang_harness.types import InboundEvent
from tangtang_harness.business.db import Database


def setup(tmp_path, **options):
    profile = ModelProfile('test', 'Test', 'custom', 'synthetic', 'https://example.invalid/v1', cache_key_enabled=True)
    options['extra'] = {**options.get('extra', {}), 'context_mode': 'legacy'}
    config = HarnessConfig(root=tmp_path, profiles=(profile,), active_model='test', **options)
    return config, Store(tmp_path)


def event(mid, user=101, text='普通消息', **options):
    return InboundEvent(mid, 999, user, 201, text, sender={'nickname': '合成群友'}, **options)


def blacklist(root, users):
    path = root / 'runtime' / 'business.db'
    db = Database(path)
    db.seed_groups((201,))
    with db.connect() as conn:
        conn.execute('DELETE FROM active_filters')
    db.add_filter_members('active', users, 101)


def test_blacklist_added_after_delivery_invalidates_snapshot_and_keeps_originals(tmp_path):
    config, store = setup(tmp_path)
    blocked = event('old', 102, '待过滤原文')
    turn_id = store.confirm_turn(blocked, ['待过滤回答'], 'r', ['1'], '待过滤原文')
    store.publish_snapshot(blocked.session_key, {'facts': ['待过滤摘要']}, turn_id)
    blacklist(tmp_path, [102])
    built = build_context(config, store, event('now'), config.profile())
    assert all('待过滤' not in encode(row) for row in built.messages)
    assert built.snapshot_revision == 0 and built.telemetry['excluded_turn_ids'] == [turn_id]
    assert store.history(blocked.session_key)[0]['messages'] == ['待过滤回答']
    assert store.snapshot(blocked.session_key)['content'] == {'facts': ['待过滤摘要']}


def test_current_and_retained_quotes_from_blacklisted_author_are_excluded(tmp_path):
    config, store = setup(tmp_path)
    quoted = {'user_id': 102, 'text': '引用禁用资料'}
    previous = event('old', quoted=quoted)
    old = build_context(config, store, previous, config.profile())
    turn_id = store.confirm_turn(previous, ['历史回复'], 'r', ['1'], old.user_content)
    blacklist(tmp_path, [102])
    built = build_context(config, store, event('now', quoted=quoted), config.profile())
    assert '引用禁用资料' not in encode(built.messages)
    assert built.telemetry['excluded_turn_ids'] == [turn_id]


def test_unknown_imported_authors_are_excluded_only_while_blacklist_exists(tmp_path):
    config, store = setup(tmp_path)
    with store.connect() as conn:
        conn.execute("INSERT INTO turns(session_key,event_key,request_id,user_content,messages,message_ids,status,created_at) VALUES('group:201','import:unknown','r',?,?,?,'delivered',1)",
                     (encode('未验证作者旧历史'), encode(['旧回复']), encode(['1'])))
    assert '未验证作者旧历史' in encode(build_context(config, store, event('before'), config.profile()).messages)
    blacklist(tmp_path, [102])
    assert '未验证作者旧历史' not in encode(build_context(config, store, event('after'), config.profile()).messages)


def test_removing_blacklist_rebuilds_filtered_snapshot_and_changes_cache_key(tmp_path):
    config, store = setup(tmp_path)
    allowed = store.confirm_turn(event('a'), ['可用回答'], 'a', ['1'], '可用原文')
    hidden = store.confirm_turn(event('b', 102), ['恢复后回答'], 'b', ['2'], '恢复后原文')
    blacklist(tmp_path, [102])
    store.publish_snapshot('group:201', {'facts': ['过滤前的摘要']}, hidden)
    during = build_context(config, store, event('now'), config.profile())
    # An unsafe covered turn still invalidates the snapshot; originals remain available.
    assert during.snapshot_revision == 0
    store.publish_snapshot('group:201', {'facts': ['过滤后的摘要']}, hidden, rebuild=True, filter_users=[102])
    during = build_context(config, store, event('now'), config.profile())
    assert during.snapshot_revision == 2
    blacklist(tmp_path, [])
    after = build_context(config, store, event('now'), config.profile())
    assert after.snapshot_revision == 0 and '恢复后原文' in encode(after.messages)
    assert after.payload['prompt_cache_key'] != during.payload['prompt_cache_key']


def test_queued_compaction_rechecks_scope_changes_and_blacklist(tmp_path):
    config, store = setup(tmp_path, recent_rounds=1)
    service = ChatService(config, store)
    for mid, user in [('a', 102), ('b', 101), ('c', 101)]:
        store.confirm_turn(event(mid, user, '原文' + mid), ['回复' + mid], mid, [mid], '原文' + mid)
    job_id = service.enqueue_compaction('group:201')
    job = next(row for row in store.jobs() if row['id'] == job_id)
    blacklist(tmp_path, [102])
    source, _, _ = service._bounded_source(job, config.profile(), '整理完成历史')
    assert [turn['event_key'] for turn in source['turns']] == [event('b').key]
    store.set_setting('event_scope:' + event('b').key, {'chat_allowed': False})
    import pytest
    with pytest.raises(ValueError, match='无符合范围'):
        service._bounded_source(job, config.profile(), '整理完成历史')


def test_previous_image_filter_does_not_search_further_back_and_quoted_image_is_removed(tmp_path):
    _, store = setup(tmp_path)
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(buffer, format='PNG')
    url = 'base64://' + base64.b64encode(buffer.getvalue()).decode()
    segment = {'type': 'image', 'data': {'file': url}}
    store.append_event(event('older', 103, segments=(segment,)))
    store.append_event(event('previous', 102, segments=(segment,)))
    blacklist(tmp_path, [102])
    current = event('now', quoted={'user_id': 102, 'segments': [segment]})
    resolved = asyncio.run(MediaResolver(store).resolve(current))
    assert not resolved.parts and not resolved.assets


def test_snapshot_context_does_not_load_covered_image_bodies_or_query_each_turn(tmp_path, monkeypatch):
    config, store = setup(tmp_path)
    for index in range(20):
        cutoff = store.confirm_turn(event(str(index)), ['旧回复'], str(index), [str(index)], '旧原文')
    store.publish_snapshot('group:201', {'facts': ['摘要']}, cutoff)
    observed = []
    original = store.history
    def history(session, **options):
        observed.append(options['after_id'])
        return original(session, **options)
    monkeypatch.setattr(store, 'history', history)
    built = build_context(config, store, event('now'), config.profile())
    assert observed == [cutoff] and built.snapshot_revision == 1
