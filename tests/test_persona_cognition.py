"""Cross-scene timelines, delivery ambiguity and durable replay contracts."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bot.services.persona_actions import PersonaActions
from bot.services.persona_capacity import EndpointCapacity
from bot.services.persona_cognition import CognitionStore
from bot.services.persona_inbox import ObservationInbox
from bot.services.tangtang_db import TangtangDb


def source(key='101:1', user=201, text='我在修改画稿', attribution='direct', occurred=100):
    group, mid = key.split(':')
    return dict(event_key=key, group_id=int(group), user_id=user, message_id=mid, text=text,
                attribution=attribution, occurred_at=occurred, received_at=occurred,
                revision=1, route_version='1:core')


def claim(s, statement='愿意分享创作过程', kind='impression', topic='创作', **extra):
    return dict(kind=kind, topic=topic, statement=statement, applicability='讨论创作时',
                assertion_type='self_report' if kind == 'fact' else 'inference',
                evidence=[dict(event_key=s['event_key'], quote=s['text'])], **extra)


@pytest.fixture
def store(tmp_path):
    return CognitionStore(TangtangDb(tmp_path / 'denia.db'))


def test_impression_global_with_evidence_and_other_user_isolated(store):
    from tests.test_persona_profiles import publish
    s = source(attribution='ambient')
    publish(store, s, '愿意分享创作过程')
    cross = store.snapshot('next', 201, 102, [], '画稿', 120)
    assert cross.claims[0]['content'] == '愿意分享创作过程'
    assert cross.claims[0]['confidence'] == .3
    assert not store.snapshot('stranger', 202, 102, []).claims
    assert '我在修改画稿' in store.own_impression(201)
    assert '我在修改画稿' not in store.own_impression(202)


def test_correction_versions_opposing_evidence_and_late_message(store):
    s = source(text='我喜欢画画')
    store.merge({'claims': [claim(s, s['text'], 'fact')]}, store.snapshot('one', 201, 101, [s]), 100)
    later = source('102:2', text='我现在不喜欢画画了', occurred=200)
    raw = claim(later, later['text'], 'fact', id=1, expected_version=1, operation='revise')
    raw['evidence'][0]['stance'] = 'opposes'
    result = store.merge({'claims': [raw]}, store.snapshot('two', 201, 102, [later]), 201)
    assert not result.rejected
    assert result.expected['1'] == 2
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM person_semantic_versions').fetchone()[0] == 2
        assert c.execute('SELECT count(*) FROM persona_claim_support').fetchone()[0] == 2
    old = source('101:3', text='我喜欢画画', occurred=150)
    stale = claim(old, old['text'], 'fact', id=1, expected_version=2, operation='revise')
    rejected = store.merge({'claims': [stale]}, store.snapshot('late', 201, 101, [old]), 210)
    assert rejected.rejected == ['out_of_order_correction']


def test_no_cross_user_evidence_and_no_fictional_fact(store):
    s = source(user=202)
    result = store.merge({'claims': [claim(s)]}, store.snapshot('bad', 201, 101, [s]))
    assert result.rejected == ['unattributed_evidence']
    s = source(text='她说“我在修改画稿”')
    result = store.merge({'claims': [claim(s, s['text'], 'fact')]}, store.snapshot('quote', 201, 101, [s]))
    assert result.rejected == ['not_asserted_by_subject']


def test_ambient_upgrade_does_not_duplicate_evidence(store):
    s = source(attribution='ambient')
    store.merge({'claims': [claim(s, s['text'], 'fact')]}, store.snapshot('a', 201, 101, [s]))
    s.update(attribution='direct', revision=2)
    store.merge({'claims': [claim(s, s['text'], 'fact')]}, store.snapshot('b', 201, 101, [s]))
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM persona_claim_support').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM person_semantic_versions').fetchone()[0] == 1
        assert c.execute('SELECT attribution FROM persona_sources').fetchone()[0] == 'direct'


def test_caused_state_targets_person_decays_and_no_silence_inference(store):
    s = source(text='这个玩笑让我不舒服')
    patch = dict(topic='玩笑', label='谨慎一些', strength=-.3, half_life=60,
                 evidence=[dict(event_key=s['event_key'], quote=s['text'])])
    store.merge({'states': [patch]}, store.snapshot('one', 201, 101, [s]), 100)
    assert store.snapshot('two', 201, 102, [], now=160).states[0]['strength'] == -.15
    assert not store.snapshot('three', 202, 101, [], now=160).states
    assert not store.snapshot('late', 201, 101, [], now=10000).states
    assert store.merge({'states': [{**patch, 'evidence': []}]}, store.snapshot('silent', 201, 101, [])).rejected


def test_global_attention_continues_across_people_without_transferring_hostility(store):
    s = source(text='这个复杂问题需要你多想一会儿')
    patch = dict(topic='复杂讨论', label='集中注意', strength=.4, half_life=300,
                 target='self', dimension='attention', evidence=[dict(event_key=s['event_key'], quote=s['text'])])
    assert not store.merge({'states': [patch]}, store.snapshot('one', 201, 101, [s]), 100).rejected
    assert store.snapshot('other', 202, 102, [], now=100).states[0]['strength'] == .15
    patch['dimension'] = 'resentment'
    assert store.merge({'states': [patch]}, store.snapshot('invalid', 201, 101, [s]), 100).rejected


def create_intent(store):
    s = source(text='我明天改完画稿再给你看看')
    patch = dict(topic='画稿修改', description='下次见面问问修改情况', state='open',
                 evidence=[dict(event_key=s['event_key'], quote=s['text'])])
    assert not store.merge({'intents': [patch]}, store.snapshot('one', 201, 101, [s]), 100).rejected
    return '201:画稿修改'


def test_intent_cross_group_reservation_and_actual_delivery(store):
    iid = create_intent(store)
    actions = PersonaActions(store)
    assert actions.prepare('turn-a', 201, 101, ['画稿怎么样啦', '有新图吗'], {}, intent_id=iid, now=110)
    assert not actions.prepare('turn-b', 201, 102, ['改好了吗'], {}, intent_id=iid, now=110)
    assert actions.start('turn-a', 0)
    actions.result('turn-a', 0, message_id='301')
    assert actions.start('turn-a', 1)
    actions.result('turn-a', 1, error='timeout')
    assert actions.diagnostics()['actions'] == {'unknown': 1}
    assert actions.diagnostics()['intents'] == {'reserved': 1}
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM persona_exposures').fetchone()[0] == 1
        assert c.execute("SELECT summary FROM persona_episodes WHERE outcome='delivered_not_read'").fetchone()[0] == '画稿怎么样啦'
    assert not actions.prepare('turn-a', 201, 101, ['重发'], {})
    actions.result('turn-a', 0, message_id='301')
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM persona_exposures').fetchone()[0] == 1


def test_crash_after_send_start_is_unknown(store):
    actions = PersonaActions(store)
    assert actions.prepare('crash', 201, 101, ['答复'], {})
    assert actions.start('crash', 0)
    actions.recover()
    assert actions.diagnostics()['actions'] == {'unknown': 1}
    assert not actions.start('crash', 0)


def test_unrelated_update_does_not_cancel_reply_but_correction_does(store):
    a, b = source(), source('102:2', user=202)
    store.merge({'claims': [claim(a, a['text'], 'fact')]}, store.snapshot('one', 201, 101, [a]))
    actions = PersonaActions(store)
    assert actions.prepare('send', 201, 101, ['你的画稿'], {'1': 1})
    store.merge({'claims': [claim(b, b['text'], 'fact')]}, store.snapshot('other', 202, 102, [b]))
    assert store.current({'1': 1})
    newer = source('102:3', text='我不喜欢分享画稿', occurred=200)
    store.merge({'claims': [claim(newer, newer['text'], 'fact', id=1, expected_version=1, operation='revise')]},
                store.snapshot('fix', 201, 102, [newer]))
    assert not actions.start('send', 0)
    assert actions.diagnostics()['actions'] == {'superseded': 1}


def test_inbox_transaction_lease_upgrade_and_target_before_ack(tmp_path, store):
    db = TangtangDb(tmp_path / 'source.db')
    inbox = ObservationInbox(db)
    db.insert_group_message(group_id=101, user_id=201, nickname='甲', text='我在修改画稿',
        message_id='1', created_at='2026-09-19T00:00:00+00:00',
        observation=dict(persona='denia', route_version='1:core', occurred_at=100, received_at=100))
    leased = inbox.claim(now=110, active_groups={101})
    snap = store.snapshot('background', 201, 101, leased)
    store.merge({'claims': [claim(leased[0])]}, snap)
    # Crash between target commit and source acknowledgement: replay receipt
    # proves the target is durable without adding another evidence event.
    replay = inbox.claim(now=210, active_groups={101})
    assert replay[0]['epoch'] > leased[0]['epoch']
    assert store.processed(replay)
    inbox.acknowledge(leased, now=211)
    assert inbox.diagnostics(211)['states'] == {'processing': 1}
    direct = inbox.sources(101, ['1'], 'denia', direct=True)
    inbox.acknowledge(replay, now=212)
    assert inbox.diagnostics(212)['states'] == {'pending': 1}
    assert direct[0]['revision'] == 2
    assert not store.processed(direct)


def test_pending_user_fairness_and_capture_rollback(tmp_path):
    db = TangtangDb(tmp_path / 'source.db')
    for i in range(30):
        db.insert_group_message(group_id=101, user_id=201 if i < 29 else 202, nickname='群友',
            text='我在画画', message_id=str(i), created_at='2026-09-19T00:00:00+00:00',
            observation=dict(persona='denia', route_version='core', occurred_at=100+i, received_at=100+i))
    inbox = ObservationInbox(db)
    batch = inbox.claim(now=200, active_groups={101})
    assert len(batch) == 12 and batch[0]['user_id'] == 201
    inbox.acknowledge(batch, now=201)
    quiet = inbox.claim(now=202, active_groups={101})
    assert quiet[0]['user_id'] == 202
    with pytest.raises(Exception):
        db.insert_group_message(group_id=101, user_id=203, nickname='丙', text='不应半写入',
            message_id='broken', created_at='2026-09-19T00:00:00+00:00',
            observation=dict(persona=None, route_version='core', occurred_at=100, received_at=100))
    with db._connect() as conn:
        assert not conn.execute("SELECT 1 FROM tangtang_group_messages WHERE message_id='broken'").fetchone()


def test_parallel_same_intent_has_one_winner(store):
    iid = create_intent(store)
    def attempt(i):
        return PersonaActions(store).prepare(f'parallel-{i}', 201, 101+i, ['画稿怎么样啦'], {}, intent_id=iid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(16))) == 1


def test_quoted_delivery_feedback_requires_real_receipt(store):
    actions = PersonaActions(store)
    actions.prepare('feedback', 201, 101, ['一个玩笑'], {})
    actions.start('feedback', 0)
    actions.result('feedback', 0, message_id='301')
    feedback = {**source('101:2', text='这个玩笑不合适'), 'reply_to': '301'}
    store.snapshot('response', 201, 101, [feedback])
    unrelated = {**source('102:3', text='我不喜欢'), 'reply_to': '301'}
    store.snapshot('unrelated', 201, 102, [unrelated])
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM persona_feedback').fetchone()[0] == 1


def test_capacity_reserves_foreground_and_cancellation_releases():
    asyncio.run(_capacity_scenario())


async def _capacity_scenario():
    capacity = EndpointCapacity(total=2, background=1)
    async with capacity.acquire(True):
        async with asyncio.timeout(.1):
            async with capacity.acquire(False):
                assert capacity.active == 2
        entered = asyncio.Event()
        async def blocked_background():
            async with capacity.acquire(True):
                entered.set()
        waiting = asyncio.create_task(blocked_background())
        await asyncio.sleep(0)
        assert not entered.is_set()
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    assert capacity.active == capacity.background_active == 0
