"""Cost control must preserve corrections, source coverage and recoverability."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from bot.services.background_work import BackgroundWork, WorkDeferred
from bot.services.group_summary import GroupSummaryService, GroupSummaryWorker
from bot.services.group_summary_batch import parse_batch
from bot.services.persona_capacity import EndpointCapacity, provider_post, _loops
from bot.services.persona_capacity import background_request, request_timeout_seconds
from bot.services.persona_capacity import CapacityUnavailable
from bot.services.persona_cognition import CognitionStore
from bot.services.persona_inbox import ObservationInbox
from bot.services.persona_profile_store import ProfileStore
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_db import TangtangDb
from tests.test_group_summary import Loader, SummaryProvider
from tests.test_tangtang_chat import enabled_config
from tests.test_persona_cognition import source, claim
from tests.test_persona_profiles import draft_for, review_for


def test_hourly_budget_is_shared_after_restart_and_failures_still_count(tmp_path):
    store = PersonaStore(tmp_path / 'state.db')
    store.set_option('background_work_policy', {'summary_requests': 2, 'summary_tokens': 100})
    work = BackgroundWork(store)
    one = work.reserve('summary', 'batch', 20, False, 60, now=100)
    with pytest.raises(WorkDeferred):
        work.reserve('summary', 'batch2', 20, False, 60, now=101)
    work.finish(one, 'returned', {'total_tokens': 20})
    two = work.reserve('summary', 'batch2', 20, False, 60, now=102)
    work.finish(two, 'TimeoutError')
    restarted = BackgroundWork(store)
    with pytest.raises(WorkDeferred):
        restarted.reserve('summary', 'batch3', 20, False, 1, now=103)
    assert restarted.reserve('summary', 'batch3', 20, False, 60, now=3703)


def test_historical_cap_does_not_block_new_evidence(tmp_path):
    store = PersonaStore(tmp_path / 'state.db')
    store.set_option('background_work_policy', {'history_requests': 1})
    work = BackgroundWork(store)
    work.reserve('profile', 'old', 48, True, 10, now=100)
    with pytest.raises(WorkDeferred):
        work.reserve('memory', 'old', 48, True, 10, now=101)
    assert work.reserve('memory', 'new', 1, False, 10, now=101)


def test_queue_wait_does_not_consume_execution_timeout():
    async def run():
        endpoint = 'https://example.invalid/responses'
        capacity = EndpointCapacity(total=1)
        _loops[asyncio.get_running_loop()] = {endpoint: capacity}
        entered = asyncio.Event()
        class Client:
            timeout = SimpleNamespace(read=.15)
            async def post(self, endpoint, **kwargs):
                await asyncio.sleep(.08)
                return 'ok'
        async def occupied():
            async with capacity.acquire():
                entered.set()
                await asyncio.sleep(.15)
        blocker = asyncio.create_task(occupied())
        await entered.wait()
        assert await provider_post(Client(), endpoint) == 'ok'
        await blocker
    asyncio.run(run())


def test_only_background_requests_get_their_extended_proxy_deadline():
    assert request_timeout_seconds(120) == 30
    token = background_request.set(True)
    try:
        assert request_timeout_seconds(120) == 120
        assert request_timeout_seconds(10) == 10
    finally:
        background_request.reset(token)


def test_local_queue_timeout_is_not_a_billed_provider_failure(tmp_path):
    store = PersonaStore(tmp_path / 'state.db')
    store.set_option('background_work_policy', {'summary_requests': 1})
    work = BackgroundWork(store)
    class Busy:
        async def generate(self, *args):
            raise CapacityUnavailable
    async def run():
        with pytest.raises(WorkDeferred):
            await work.generate(Busy(), SimpleNamespace(max_output_tokens=100), 'system', 'prompt',
                                kind='summary', sources=[])
    asyncio.run(run())
    assert work.reserve('summary', 'next', 1, False, 100)
    with store.connect() as conn:
        assert tuple(conn.execute("SELECT status,charged_tokens FROM background_work_calls WHERE status='deferred_queue'").fetchone()) == ('deferred_queue', 0)


def add_message(db, mid, *, group=101, user=201, received=100, direct=False):
    db.insert_group_message(group_id=group, user_id=user, nickname='member', text='我现在不喝咖啡了',
        message_id=str(mid), created_at='2026-09-19T10:00:00+08:00',
        observation=dict(persona='denia', route_version='1:core', occurred_at=received,
                         received_at=received, attribution='direct' if direct else 'ambient'))


def test_memory_batches_same_person_and_group_with_live_priority(tmp_path):
    db = TangtangDb(tmp_path / 'raw.db')
    add_message(db, 1, received=10)
    add_message(db, 2, group=102, received=100010, direct=True)
    add_message(db, 3, group=102, user=202, received=100010)
    inbox = ObservationInbox(db)
    rows = inbox.claim(now=100020, active_groups={101, 102}, wait_seconds=180, limit=48)
    assert [r['event_key'] for r in rows] == ['102:2']
    inbox.defer(rows, now=100020)
    with db._connect() as conn:
        row = conn.execute("SELECT state,attempts FROM persona_observation_inbox WHERE event_key='102:2'").fetchone()
    assert tuple(row) == ('pending', 0)


def test_ambient_waits_but_single_direct_correction_is_immediate(tmp_path):
    db = TangtangDb(tmp_path / 'raw.db')
    add_message(db, 1)
    inbox = ObservationInbox(db)
    assert not inbox.claim(now=110, active_groups={101}, wait_seconds=180)
    assert len(inbox.claim(now=281, active_groups={101}, wait_seconds=180)) == 1
    add_message(db, 2, user=202, received=280, direct=True)
    assert len(inbox.claim(now=283, active_groups={101}, wait_seconds=180)) == 1


def test_sql_conflict_is_rejected_without_losing_other_valid_patches(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    s = source(text='我喜欢画画')
    first = claim(s, s['text'], 'fact')
    assert cognition.merge({'claims': [first]}, cognition.snapshot('a', 201, 101, [s])).accepted
    conflict = {**first, 'applicability': '另一场景'}
    s2 = source('101:2', text='我喜欢喝茶', occurred=200)
    good = claim(s2, s2['text'], 'fact', topic='饮食')
    result = cognition.merge({'claims': [conflict, good]}, cognition.snapshot('b', 201, 101, [s, s2]))
    assert result.rejected == ['memory_constraint_conflict']
    assert len(result.accepted) == 1
    assert not cognition.processed([s2])


def test_review_content_failure_rebuilds_draft_and_keeps_attempt_limit(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    s = source()
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    for now in (100, 200, 300):
        batch = profiles.claim(now=now)
        assert batch.stage == 'draft'
        profiles.save_draft(batch, draft_for(s), now=now)
        profiles.fail(batch, 'previous_observation_not_preserved', now=now, terminal=True)
    with cognition.connect() as conn:
        row = conn.execute('SELECT state,attempts,draft FROM persona_profile_jobs').fetchone()
    assert tuple(row) == ('failed', 3, '')


def test_long_evidence_review_retry_keeps_exact_page(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    s = source(text='我在修改画稿' + '甲' * 3400)
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim(now=100)
    draft = draft_for({**s, 'text': '我在修改画稿'})
    profiles.save_draft(batch, draft, now=100)
    profiles.fail(batch, 'ReadTimeout', now=100)
    resumed = profiles.claim(now=200)
    assert resumed.stage == 'review'
    assert resumed.sources == batch.sources


def test_unchanged_profile_acknowledges_without_new_version(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    s = source()
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    draft = draft_for(s)
    profiles.publish(batch, draft, review_for(draft))
    cognition.import_sources([source('101:2', text='嗯嗯')])
    batch = profiles.claim()
    assert profiles.acknowledge_unchanged(batch, batch.previous)
    with cognition.connect() as conn:
        assert conn.execute('SELECT count(*) FROM persona_profile_versions').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM persona_profile_seen').fetchone()[0] == 2


def test_profile_live_batch_does_not_sweep_in_historical_backlog(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    cognition.import_sources([source(f'101:{i}', occurred=i) for i in range(1, 70)])
    cognition.import_sources([source('101:100', text='我现在改喝茶', occurred=100000)])
    batch = ProfileStore(cognition).claim(now=100001)
    assert [s['event_key'] for s in batch.sources] == ['101:100']


def test_prior_reference_does_not_mark_unread_middle_as_processed(tmp_path):
    cognition = CognitionStore(TangtangDb(tmp_path / 'memory.db'))
    s = source(text='我在修改画稿' + '甲' * 3400)
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    first = profiles.claim(now=100)
    draft = draft_for({**s, 'text': '我在修改画稿'})
    profiles.publish(first, draft, review_for(draft), now=100)
    cognition.import_sources([source('101:2', text='嗯嗯', occurred=100000)])
    batch = profiles.claim(now=100001)
    reference = next(s for s in batch.sources if s['event_key'] == '101:1')
    assert reference['coverage_eligible'] is False
    assert profiles.acknowledge_unchanged(batch, batch.previous)
    with cognition.connect() as conn:
        assert conn.execute("SELECT covered_chars FROM persona_profile_seen WHERE event_key='101:1'").fetchone()[0] == 1500


@pytest.mark.parametrize('ids,ignored', [([1], []), ([1, 1], [2]), ([1, 3], []), ([True], [2])])
def test_summary_requires_exactly_once_source_coverage(ids, ignored):
    with pytest.raises(ValueError):
        parse_batch(json.dumps({'updates': [{'topic_id': 0, 'message_ids': ids}], 'ignored_message_ids': ignored}), [1, 2], set())


def test_summary_atomic_rollback_and_idempotent_commit(tmp_path):
    db = TangtangDb(tmp_path / 'raw.db')
    common = dict(title='安排', summary='周六三点', keywords=(), participants=(), unresolved=(), state='active')
    updates = [{**common, 'topic_id': None, 'message_ids': (1,)}, {**common, 'topic_id': 999, 'message_ids': (2,)}]
    with pytest.raises(ValueError):
        db.group_summary_commit_batch(101, updates, [1, 2], now='now')
    assert not db.group_summary_sources(101)
    updates = updates[:1]
    assert db.group_summary_commit_batch(101, updates, [1, 2], now='now')
    assert not db.group_summary_commit_batch(101, updates, [1, 2], now='now')
    assert len(db.group_summary_sources(101)) == 1


def test_summary_wait_and_failure_backoff_survive_worker_restart(tmp_path):
    db = TangtangDb(tmp_path / 'raw.db')
    add_message(db, 1)
    class Broken(SummaryProvider):
        async def generate(self, *args):
            self.calls += 1
            raise TimeoutError
    provider = Broken()
    clock = ['2026-09-19T10:01:00+08:00']
    service = GroupSummaryService(db, provider, Loader(enabled_config()), chat_id=lambda: clock[0])
    assert asyncio.run(GroupSummaryWorker(service).tick([101])) == 0
    assert provider.calls == 0
    clock[0] = '2026-09-19T10:05:00+08:00'
    assert asyncio.run(GroupSummaryWorker(service).tick([101])) == 0
    assert provider.calls == 1
    assert asyncio.run(GroupSummaryWorker(service).tick([101])) == 0
    assert provider.calls == 1
    assert len(db.group_summary_pending(101)) == 1


def test_summary_batches_large_backlog_without_skipping_the_tail(tmp_path):
    db = TangtangDb(tmp_path / 'raw.db')
    for mid in range(1, 101):
        add_message(db, mid)
    provider = SummaryProvider()
    service = GroupSummaryService(db, provider, Loader(enabled_config()),
                                  chat_id=lambda: '2026-09-19T10:05:00+08:00')
    assert asyncio.run(GroupSummaryWorker(service, batch_messages=200).tick([101])) == 60
    assert provider.calls == 1
    pending = db.group_summary_pending(101)
    assert len(pending) == 40 and pending[0]['id'] == 61
