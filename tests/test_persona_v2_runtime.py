"""Exercise real chat, source inbox, migration and worker together without QQ."""
from dataclasses import replace
from types import SimpleNamespace
import json
import time

import pytest

from bot.services.persona_actions import PersonaActions
from bot.services.persona_cognition_migration import migrate_legacy
from bot.services.persona_inbox import ObservationInbox
from bot.services.persona_observer import PersonaObserver
from tests.test_persona_integration import async_test, event, make_runtime, Provider, service_for


def runtime(tmp_path, output):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    engine.store.switch(1002, 'denia')
    engine.store.set_option('denia_v2_enabled', True)
    provider = Provider(output)
    service, config = service_for(tmp_path, engine, provider)
    return engine, service, replace(config, memory_enabled=True), provider


def capture(service, engine, config, ev, direct=True):
    context = engine.snapshot(ev, config.model, False)
    service.record_group_message(ev.group_id, '群友', ev.get_plaintext(), user_id=ev.user_id,
        message_id=ev.message_id, observation=dict(persona='denia',
        route_version=f'{context.selection_revision}:{context.persona.version}',
        occurred_at=time.time() - 5, received_at=time.time() - 5,
        attribution='direct' if direct else 'ambient'))


def output(decision='reply'):
    return {'decision': decision, 'messages': ['画稿可以慢慢改'], 'voice': 'text',
        'claims': [{'kind': 'fact', 'topic': '创作', 'statement': '我在修改画稿',
                    'assertion_type': 'self_report', 'applicability': '创作时',
                    'evidence': [{'event_key': '1001:1', 'quote': '我在修改画稿'}]}]}


def test_diagnostics_preserve_source_queue_when_persona_db_has_empty_inbox(tmp_path):
    from bot.services.tangtang_db import TangtangDb
    from bot.services.persona_inbox import capture as capture_observation
    from scripts.report_persona_memory import report

    source = TangtangDb(tmp_path / 'data/tangtang/tangtang.db')
    TangtangDb(tmp_path / 'data/personas/denia-history.db')
    with source._connect() as conn:
        capture_observation(conn, persona='denia', route_version='test', group_id=1001,
            user_id=2001, message_id='1', text='我在修改画稿',
            occurred_at=time.time() - 60, received_at=time.time() - 60)
    result = report(tmp_path)
    assert result['persona_observation_inbox'] == {'pending': 1}
    assert result['pending_users'] == 1
    assert result['protected_characters'] == len('我在修改画稿')
    assert result['oldest_pending_seconds'] >= 60


@pytest.mark.parametrize('decision,ack', [('reply', True), ('reply', False), ('observe', True)])
@async_test
async def test_memory_survives_silence_or_failed_send(tmp_path, monkeypatch, decision, ack):
    engine, service, config, provider = runtime(tmp_path, output(decision))
    ev = event('娅娅，记住我在修改画稿')
    capture(service, engine, config, ev)
    sent = []
    async def send(*args, **kwargs):
        sent.append(kwargs)
        return {'message_id': 4001} if ack else {}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    await service.handle(SimpleNamespace(), ev, config)
    store = engine.cognition('denia', service._base_db)
    assert any(c['content'] == '我在修改画稿' for c in store.snapshot('saved', ev.user_id, 1001, []).claims)
    assert not store.snapshot('cross', ev.user_id, 1002, []).claims
    states = PersonaActions(store).diagnostics()['actions']
    if decision == 'observe':
        assert not sent and not states
    else:
        assert len(sent) == 1
        assert states == {'confirmed' if ack else 'unknown': 1}
    with store.connect() as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='person_impression_events'").fetchone():
            assert conn.execute('SELECT count(*) FROM person_impression_events').fetchone()[0] == 0
    assert '即时个人印象' not in provider.seen[0][1]


@async_test
async def test_own_impression_view_does_not_call_model(tmp_path, monkeypatch):
    engine, service, config, provider = runtime(tmp_path, output('observe'))
    first = event('娅娅，记住我在修改画稿')
    capture(service, engine, config, first)
    await service.handle(SimpleNamespace(), first, config)
    from tests.test_persona_profiles import publish
    store = engine.cognition('denia', service._base_db)
    with store.connect() as conn:
        evidence = dict(conn.execute('SELECT * FROM persona_sources WHERE user_id=?', (first.user_id,)).fetchone())
    publish(store, evidence, '本次想完善作品')
    sent = []
    async def send(*args, **kwargs):
        sent.append(str(kwargs['message']))
        return {'message_id': 4001}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    viewing = event('娅娅，你对我有什么印象？', group=1001, message=2)
    capture(service, engine, config, viewing)
    await service.handle(SimpleNamespace(), viewing, config)
    assert len(provider.seen) == 1
    assert '完善作品' in sent[0]


@async_test
async def test_background_no_daily_budget_and_quarantines_invalid_evidence(tmp_path):
    engine, service, config, _ = runtime(tmp_path, output())
    engine.store.set_option('background_global_limit', 0)
    engine.store.set_option('background_work_policy', {'memory_wait_seconds': 2})
    ev = event('记住我在修改画稿')
    capture(service, engine, config, ev, direct=False)
    class Extractor:
        calls = 0
        async def generate(self, *args):
            self.calls += 1
            return json.dumps(output('observe'), ensure_ascii=False), {'total_tokens': 80}
    provider = Extractor()
    worker = PersonaObserver(engine, service._base_db, provider, SimpleNamespace(load=lambda: config))
    await worker.tick({1001})
    assert provider.calls == 1
    assert ObservationInbox(service._base_db).diagnostics()['states'] == {'applied': 1}
    await worker.tick({1001})
    assert provider.calls == 1
    assert engine.cognition('denia', service._base_db).snapshot('saved', ev.user_id, 1001, []).claims[0]['content'] == '我在修改画稿'


def test_legacy_migration_preserves_history_and_is_idempotent(tmp_path):
    engine, service, config, _ = runtime(tmp_path, output())
    memory = engine.memory('denia', service._base_db, service._now)
    memory.observe_user_message(group_id=1001, user_id=2001, message_id='old', text='记住我喜欢草莓')
    memory.impressions.record(2001, 1001, 'impression', '我还想把画稿改得更好',
        [{'trait': 'persistent', 'direction': 1, 'quote': '我还想把画稿改得更好'}], service._now())
    store = engine.cognition('denia', service._base_db)
    first = migrate_legacy(store, engine.store)
    assert first['facts'] == first['impressions'] == 1
    assert not any(migrate_legacy(store, engine.store).values())
    snapshot = store.snapshot('next', 2001, 1001, [])
    assert any('草莓' in c['content'] for c in snapshot.claims)
    assert '持续推进' not in store.own_impression(2001)
    assert not any(c['kind'] == 'impression' for c in snapshot.claims)
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM person_facts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM person_impression_events').fetchone()[0] == 1


@async_test
async def test_partial_rejection_keeps_source_for_worker_and_repairs_reply(tmp_path, monkeypatch):
    bad = output()
    bad['claims'].append({**bad['claims'][0], 'topic': '虚构', 'evidence': [{'event_key': 'fake', 'quote': '未说过'}]})
    engine, service, config, provider = runtime(tmp_path, bad)
    ev = event('娅娅，记住我在修改画稿')
    capture(service, engine, config, ev)
    original_generate = provider.generate_agent
    async def generate(*args):
        result = await original_generate(*args)
        provider.response = dict(decision='reply', messages=['慢慢改就好'], voice='text', claims=[], states=[], intents=[])
        return result
    provider.generate_agent = generate
    sent = []
    async def send(*args, **kwargs):
        sent.append(str(kwargs['message']))
        return {'message_id': 4001}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    await service.handle(SimpleNamespace(), ev, config)
    assert len(provider.seen) == 2 and len(sent) == 1
    assert ObservationInbox(service._base_db).diagnostics()['states'] == {'pending': 1}
    assert any(c['content'] == '我在修改画稿' for c in engine.cognition('denia', service._base_db).snapshot('saved', ev.user_id, 1001, []).claims)


@async_test
async def test_worker_partial_rejection_is_not_acknowledged(tmp_path):
    engine, service, config, _ = runtime(tmp_path, output())
    engine.store.set_option('background_work_policy', {'memory_wait_seconds': 2})
    capture(service, engine, config, event('我在修改画稿'), direct=False)
    bad = output('observe')
    bad['states'] = [dict(topic='画稿', label='担心', evidence=[{'event_key': '1001:1', 'quote': '我在修改画稿'}])]
    class Extractor:
        async def generate(self, *args):
            return json.dumps(bad), {}
    worker = PersonaObserver(engine, service._base_db, Extractor(), SimpleNamespace(load=lambda: config))
    await worker.tick({1001})
    assert ObservationInbox(service._base_db).diagnostics()['states'] == {'retry': 1}
