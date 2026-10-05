import asyncio
from dataclasses import replace
import sqlite3
import time
from types import SimpleNamespace

import pytest

from tangtang_harness.continuation_policy import ContinuationStore
from tangtang_harness.models import ModelResult
from tangtang_harness.runtime import Runtime
import tangtang_harness.runtime as runtime_module
from tangtang_harness.types import InboundEvent, ToolResult
from test_runtime_and_console import configured, FakeBot, FakeModel


@pytest.mark.asyncio
@pytest.mark.parametrize('private_users,enabled,delivered', [([], True, True), ([101], True, True), ([105], True, False), ([], False, False)])
async def test_private_background_completion_follows_private_scope(tmp_path, private_users, enabled, delivered):
    runtime = Runtime(configured(tmp_path, 'live', private_chat_enabled=enabled,
        extra={'isolated_scope_enabled': True, 'private_user_ids': private_users}), bot=FakeBot(), model_client=FakeModel())
    checked = asyncio.Event()
    completed = []

    async def tick(**kwargs):
        checked.set()
        return [{'group_id': None, 'user_id': 101, 'self_id': 103, 'result': ToolResult('ok', '登录完成')}]

    runtime.tools.tick = tick
    runtime.tools.mark_delivered = lambda result, success: completed.append(success)
    worker = asyncio.create_task(runtime._scheduler())
    try:
        await asyncio.wait_for(checked.wait(), 2)
        await asyncio.sleep(0)
        assert bool(runtime.bot.sent) is delivered
        assert completed == ([True] if delivered else [])
        assert not runtime.chat.model_client.calls
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_quota_initialization_waits_for_connected_onebot(tmp_path):
    runtime = Runtime(configured(tmp_path, 'live'), bot=FakeBot(), model_client=FakeModel())
    runtime.bot.connected = False
    polled = asyncio.Event()

    async def poll(**kwargs):
        polled.set()

    async def groups(action, **kwargs):
        assert action == 'get_group_list'
        return [{'group_id': 102, 'member_count': 251}]

    runtime.tools.poll_external = poll
    runtime.bot.call_api = groups
    worker = asyncio.create_task(runtime._external_worker())
    try:
        await asyncio.wait_for(polled.wait(), 2)
        await asyncio.sleep(0)
        with sqlite3.connect(runtime.continuation.path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM continuation_quota_refreshes').fetchone()[0] == 0
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    runtime.bot.connected = True
    polled.clear()
    worker = asyncio.create_task(runtime._external_worker())
    try:
        await asyncio.wait_for(polled.wait(), 2)
        await asyncio.sleep(0)
        assert runtime.continuation.daily_limit(102, time.time()) == 60
        with sqlite3.connect(runtime.continuation.path) as conn:
            assert conn.execute('SELECT outcome FROM continuation_quota_refreshes').fetchone()[0] == 'completed'
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_group_membership_refreshes_at_start_and_once_per_minute(tmp_path, monkeypatch):
    runtime = Runtime(configured(tmp_path, 'live'), bot=FakeBot(), model_client=FakeModel())
    seconds = [100.0]
    requested = []

    async def poll(**kwargs):
        return None

    async def groups(action, **kwargs):
        assert action == 'get_group_list'
        requested.append(seconds[0])
        return [{'group_id': 102, 'group_name': '完整合成群名', 'member_count': 251}]

    async def advance(delay):
        seconds[0] += delay
        if seconds[0] >= 175:
            raise asyncio.CancelledError

    runtime.tools.poll_external = poll
    runtime.bot.call_api = groups
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'time', SimpleNamespace(time=lambda: 1900000000, monotonic=lambda: seconds[0]))
        patch.setattr(runtime_module.asyncio, 'sleep', advance)
        with pytest.raises(asyncio.CancelledError):
            await runtime._external_worker()
        assert requested == [100.0, 160.0]
        assert runtime.tools.db.managed_group(102)['group_name'] == '完整合成群名'
        runtime.transport_connected()
        assert runtime._last_group_membership_sync == 0
    await runtime.close()


@pytest.mark.asyncio
async def test_live_start_waits_for_membership_but_private_delivery_and_learning_continue(tmp_path):
    entered, release, scheduled, learned = (asyncio.Event() for _ in range(4))
    scopes, marked = [], []

    class Model(FakeModel):
        async def generate(self, profile, payload, *, on_delta=None):
            self.calls.append(payload)
            learned.set()
            return ModelResult('{"facts":[]}', {"input_tokens": 20, "output_tokens": 4})

    runtime = Runtime(configured(tmp_path, 'live', group_ids=(102, 104), context_mode='legacy',
        extra={'isolated_scope_enabled': True}), bot=FakeBot(), model_client=Model())
    for group in (102, 104):
        runtime.tools.domains.ensure_group(group)
    group_event = InboundEvent('group-job', 103, 101, 104, '群证据')
    private_event = InboundEvent('private-job', 103, 101, None, '私聊证据')
    group_job = runtime.chat.enqueue_memory(group_event)
    private_job = runtime.chat.enqueue_memory(private_event)

    async def groups(action, **kwargs):
        assert action == 'get_group_list'
        entered.set()
        await release.wait()
        return [{'group_id': 102, 'group_name': '仍在的完整群名'}]

    async def tick(*, group_ids, user_ids):
        scopes.append(tuple(group_ids))
        scheduled.set()
        return [{'group_id': 104, 'result': ToolResult('ok', '旧群推送')},
                {'group_id': None, 'user_id': 101, 'result': ToolResult('ok', '私聊完成')}]

    async def poll(**kwargs):
        return None

    runtime.bot.call_api = groups
    runtime.tools.tick = tick
    runtime.tools.poll_external = poll
    runtime.tools.mark_delivered = lambda result, success: marked.append((result.text, success))
    try:
        await runtime.start()
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(scheduled.wait(), 2)
        await asyncio.wait_for(learned.wait(), 2)
        assert scopes[0] == () and not runtime.accepts_background(102)
        assert marked == [('私聊完成', True)]
        assert all(event.group_id is None for event, _ in runtime.bot.sent)
        with pytest.raises(RuntimeError, match='群列表'):
            await runtime.deliver(group_event, '不应发送')
        jobs = {job['id']: job for job in runtime.store.jobs()}
        assert jobs[group_job]['status'] == 'queued'
        assert jobs[private_job]['status'] == 'completed'
        assert len(runtime.chat.model_client.calls) == 1
        release.set()
        await runtime._sync_group_membership()
        assert runtime.accepts_background(102) and not runtime.accepts_background(104)
        assert runtime.tools.db.managed_group(104, include_disabled=True)['enabled'] == 0
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_reconnect_failed_membership_keeps_history_and_suppresses_old_connection_results(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    responses = [None, [{'group_id': 102, 'group_name': '当前群'}]]
    runtime = Runtime(configured(tmp_path, 'live', group_ids=(102,),
        extra={'isolated_scope_enabled': True}), bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)

    async def groups(action, **kwargs):
        entered.set()
        await release.wait()
        return responses.pop(0)

    runtime.bot.call_api = groups
    try:
        runtime.transport_connected()
        await asyncio.wait_for(entered.wait(), 2)
        assert not runtime.accepts_background(102)
        release.set()
        await asyncio.gather(*tuple(runtime.tasks), return_exceptions=True)
        assert not runtime._group_membership_ready
        assert runtime.tools.db.is_managed_group(102)
        await runtime._sync_group_membership()
        assert runtime.accepts_background(102)
        entered.clear()
        release.clear()
        responses.append([])
        runtime.transport_connected()
        await asyncio.wait_for(entered.wait(), 2)
        runtime.transport_disconnected()
        release.set()
        await asyncio.gather(*tuple(runtime.tasks), return_exceptions=True)
        assert not runtime._group_membership_ready
        assert runtime.tools.db.is_managed_group(102)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_background_model_rechecks_connection_gate_after_paid_response(tmp_path):
    class Model(FakeModel):
        async def generate(self, profile, payload, *, on_delta=None):
            self.calls.append(payload)
            runtime.transport_disconnected()
            return ModelResult('{"facts":[{"content":"喜欢画画","quote":"我喜欢画画"}]}',
                               {'input_tokens': 20, 'output_tokens': 4})

    runtime = Runtime(configured(tmp_path, 'live', context_mode='legacy'), bot=FakeBot(), model_client=Model())
    runtime.tools.domains.ensure_group(102)
    event = InboundEvent('memory', 103, 101, 102, '我喜欢画画')
    runtime.chat.enqueue_memory(event)
    try:
        result = await runtime.chat.run_background_once(admit_job=runtime._background_job_admitted)
        assert result['status'] == 'disabled'
        assert not runtime.store.memories(event.session_key, event.user_id)
        assert runtime.store.requests()[0]['outcome'] == 'disabled'
        assert runtime.store.requests()[0]['usage']['input_tokens'] == 20
    finally:
        await runtime.close()


def test_restart_recovers_only_unfinished_quota_reservation(tmp_path):
    store = ContinuationStore(tmp_path / 'continuation.db')
    now = time.time()
    assert store.claim(102, 'attempt', now)
    assert store.begin_quota_refresh(now)
    assert not store.begin_quota_refresh(now)
    store.recover_interrupted_refreshes()
    assert store.begin_quota_refresh(now)
    store.finish_quota_refresh(now, {102: 251})
    store.recover_interrupted_refreshes()
    assert not store.begin_quota_refresh(now)
    assert store.daily_limit(102, now) == 60
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM continuation_attempts').fetchone()[0] == 1

    tomorrow = now + 86400
    assert store.begin_quota_refresh(tomorrow)
    store.finish_quota_refresh(tomorrow, None)
    store.recover_interrupted_refreshes()
    assert not store.begin_quota_refresh(tomorrow)
