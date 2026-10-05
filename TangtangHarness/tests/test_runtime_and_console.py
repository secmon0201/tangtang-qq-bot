import asyncio
from dataclasses import replace
from time import perf_counter

from fastapi.testclient import TestClient
import pytest

from tangtang_harness.app import create_app, usage_metrics
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.models import ModelResult
from tangtang_harness.onebot import NativeBot, OneBotError
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent, ToolResult
from tangtang_harness.types import ToolCall


class FakeBot:
    self_id = 103
    connected = True
    def __init__(self):
        self.sent = []
    async def send(self, event, message):
        self.sent.append((event, message))
        return {"message_id": str(1000 + len(self.sent))}
    async def call_api(self, action, **params):
        if action == "get_group_list":
            return []
        raise AssertionError("unexpected external action " + action)
    def detach(self, *args):
        self.connected = False


class FakeModel:
    def __init__(self):
        self.calls = []
    async def generate(self, profile, payload, *, on_delta=None):
        self.calls.append(payload)
        return ModelResult('{"messages":["测试答复"],"decision":"reply"}',
                           {"input_tokens": 20, "output_tokens": 4, "cache_read_tokens": 0})


def configured(tmp_path, mode="observe", *, context_mode='cache_first', **kwargs):
    model = ModelProfile("sample", "样例模型", "custom", "sample", "https://example.invalid/v1")
    kwargs['extra'] = {**kwargs.get('extra', {}), 'context_mode': context_mode}
    return HarnessConfig(root=tmp_path, mode=mode, profiles=(model,), active_model="sample", **kwargs)


def packet(text="#harness 你好", message_id=1):
    return {"post_type": "message", "message_type": "group", "group_id": 102, "user_id": 101,
            "self_id": 103, "time": 1900000000, "message_id": message_id,
            "sender": {"nickname": "测试成员", "role": "member"},
            "message": [{"type": "text", "data": {"text": text}}]}


@pytest.mark.asyncio
async def test_observe_and_out_of_scope_never_call_model_or_send(tmp_path):
    bot, model = FakeBot(), FakeModel()
    runtime = Runtime(configured(tmp_path), bot=bot, model_client=model)
    assert (await runtime.receive(packet()))["status"] == "observed"
    assert (await runtime.receive(packet()))["status"] == "duplicate"
    runtime.config = replace(runtime.config, mode="live")
    assert (await runtime.receive(packet("娅娅你好", 2)))["status"] == "observed"
    assert not bot.sent and not model.calls
    assert len(runtime.store.events("group:102")) == 2
    await runtime.close()


@pytest.mark.asyncio
async def test_live_private_scope_and_group_scope_are_independent(tmp_path):
    runtime = Runtime(configured(tmp_path, "live", group_ids=(102,),
                                extra={"test_prefix": "", "isolated_scope_enabled": True}),
                      bot=FakeBot(), model_client=FakeModel())
    private = InboundEvent("private-1", 103, 101, None, "你好")
    group = InboundEvent("group-1", 103, 101, 102, "你好")
    assert runtime.in_scope(private)
    assert runtime.in_scope(group)
    assert not runtime.in_scope(replace(group, group_id=104))
    runtime.config = replace(runtime.config, extra={"private_user_ids": [101], "test_prefix": ""})
    assert runtime.in_scope(private)
    assert not runtime.in_scope(replace(private, user_id=105))
    assert not runtime.in_scope(replace(private, user_id=105, text="#harness 你好"))
    runtime.config = replace(runtime.config, private_chat_enabled=False)
    assert not runtime.in_scope(private)
    runtime.config = replace(runtime.config, private_chat_enabled=True, mode="observe")
    assert not runtime.in_scope(private)
    await runtime.close()


@pytest.mark.asyncio
async def test_bot_leave_notice_archives_group_and_rejects_delayed_events(tmp_path):
    runtime = Runtime(configured(tmp_path, "live", group_ids=(102,),
                                extra={"test_prefix": "", "isolated_scope_enabled": True}),
                     bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102, group_name="已离开群")
    notice = {"post_type": "notice", "notice_type": "group_decrease", "sub_type": "leave",
              "group_id": 102, "user_id": 103, "self_id": 103}
    await runtime.receive(notice)
    row = runtime.tools.db.managed_group(102, include_disabled=True)
    assert row is not None and not row["enabled"]
    delayed = InboundEvent("late-1", 103, 101, 102, "延迟消息")
    assert not runtime.in_scope(delayed)
    assert not runtime.accepts_background(102)
    assert await runtime.tools.collect(delayed) is None
    runtime.store.set_setting("event_scope:" + delayed.key, {"kind": "tool"})
    await runtime.process(delayed, runtime.router.route(delayed))
    assert not runtime.chat.model_client.calls and not runtime.bot.sent
    for send in (runtime.deliver(delayed, "迟到答复"), runtime.deliver_forward(delayed, []),
                 runtime.deliver_tool(delayed, ToolResult("ok", "迟到工具答复"))):
        with pytest.raises(RuntimeError, match="离开"):
            await send
    assert (await runtime.execute_call(delayed, ToolCall("expression_send"))).status == "disabled"
    assert (await runtime.receive(packet("娅娅迟到消息", 2)))["status"] == "ignored_group_not_present"
    assert runtime.store.get_setting("event_scope:103:group:102:2")["kind"] == "group_not_present"
    await runtime.receive({**notice, "notice_type": "group_increase", "group_name": "重新加入完整群名"})
    assert runtime.in_scope(delayed) and runtime.accepts_background(102)
    assert runtime.tools.domains.domain_for_group(102) is not None
    assert runtime.tools.db.managed_group(102)["group_name"] == "重新加入完整群名"
    await runtime.close()


@pytest.mark.asyncio
async def test_authoritative_group_list_archives_missing_groups_and_preserves_records(tmp_path):
    runtime = Runtime(configured(tmp_path, "live"), bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102, group_name="原群名")
    runtime.tools.domains.ensure_group(104, group_name="离开群")
    runtime.store.append_event(InboundEvent("retained", 103, 101, 104, "保留历史"))
    assert set(runtime._reconcile_group_membership([{"group_id": 102, "group_name": "当前完整群名"}])) == {104}
    assert runtime.tools.domains.all_group_ids() == (102,)
    assert runtime.tools.db.managed_group(102)["group_name"] == "当前完整群名"
    assert runtime.store.events("group:104")[0]["payload"]["text"] == "保留历史"
    for invalid in (None, {}, [{"group_id": "invalid"}]):
        with pytest.raises(ValueError, match="群列表"):
            runtime._reconcile_group_membership(invalid)
        assert runtime.tools.domains.all_group_ids() == (102,)
    assert runtime._reconcile_group_membership([{"group_id": 102}, {"group_id": 104}]) == (104,)
    assert runtime.store.group_present(104)
    await runtime.close()


@pytest.mark.asyncio
async def test_tool_and_mixed_plan_call_counts_and_facts(tmp_path):
    bot, model = FakeBot(), FakeModel()
    runtime = Runtime(configured(tmp_path, "live"), bot=bot, model_client=model)
    async def local(event, call):
        return ToolResult("ok", "本地结果", {"facts": {"rows": [{"rank": 1, "name": "示例", "count": 5}]}})
    runtime.execute_call = local
    await runtime.receive(packet("#harness 今天发言排行"))
    await asyncio.gather(*tuple(runtime.tasks))
    assert len(bot.sent) == 1 and not model.calls
    assert runtime.store.get_setting("event_scope:103:group:102:1")["chat_allowed"] is False
    await runtime.receive(packet("#harness 今天发言排行然后分析一下", 2))
    await asyncio.gather(*tuple(runtime.tasks))
    assert len(model.calls) == 1 and len(bot.sent) == 3
    assert "本地工具真实结果" in str(model.calls[0])
    assert len(runtime.store.history("group:102")) == 1
    assert runtime.store.history("group:102")[0]["message_ids"] == ["1003"]
    await runtime.close()


@pytest.mark.asyncio
async def test_background_worker_runs_while_foreground_session_is_busy(tmp_path):
    runtime = Runtime(configured(tmp_path, "live"), bot=FakeBot(), model_client=FakeModel())
    lock = runtime.session_locks.setdefault("group:102", asyncio.Lock())
    await lock.acquire()
    called = asyncio.Event()

    async def run_once(**kwargs):
        called.set()
        return None

    runtime.chat.run_background_once = run_once
    worker = asyncio.create_task(runtime._ai_worker())
    try:
        await asyncio.wait_for(called.wait(), timeout=1)
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        lock.release()
        await runtime.close()


@pytest.mark.asyncio
async def test_incomplete_composite_does_not_partially_execute(tmp_path):
    bot, model = FakeBot(), FakeModel()
    runtime = Runtime(configured(tmp_path, "live"), bot=bot, model_client=model)
    async def forbidden(*args):
        raise AssertionError("partial plan was executed")
    runtime.execute_call = forbidden
    await runtime.receive(packet("#harness 今天发言排行然后丢给谁都行"))
    await asyncio.gather(*tuple(runtime.tasks))
    assert len(bot.sent) == 1 and not model.calls
    assert not runtime.store.tool_results("group:102")
    await runtime.close()


def test_console_preview_replay_and_distinct_experiments_are_free(tmp_path):
    runtime = Runtime(configured(tmp_path), bot=FakeBot(), model_client=FakeModel())
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.get("/api/status").json()["mode"] == "observe"
        preview = client.post("/api/preview", json={"session_key": "group:102", "text": "你好"}).json()
        assert preview["model_calls"] == preview["qq_writes"] == 0
        replay = client.post("/api/replay", json={"event": packet("你好")}).json()
        assert replay["model_calls"] == 0
        first = client.post("/api/experiments", json={"kind": "cache_append"}).json()
        second = client.post("/api/experiments", json={"kind": "prefix_change"}).json()
        assert first["model_calls"] == second["model_calls"] == 0
        assert "history" in first["result"]["diff"]["changed_layers"]
        assert "fixed" in second["result"]["diff"]["changed_layers"]
        assert not runtime.store.requests() and not runtime.chat.model_client.calls
        assert client.post("/api/experiments", json={"kind":"cold_warm","paid":True}).status_code == 400
        assert not runtime.chat.model_client.calls


def test_usage_uses_weighted_known_samples():
    rows = [
        {"usage": {"input_tokens": 100, "cache_read_tokens": 50}, "started_at": 1, "ended_at": 2},
        {"usage": {"input_tokens": 900, "cache_read_tokens": 0}, "started_at": 2, "ended_at": 3},
        {"usage": {"input_tokens": 500, "cache_read_tokens": None}, "started_at": 3, "ended_at": 4}]
    result = usage_metrics(rows)
    assert result["cache_ratio"] == .05
    assert result["coverage_ratio"] == pytest.approx(2 / 3)
    assert result["request_hit_ratio"] == .5
    assert result["cost"] is None


@pytest.mark.asyncio
async def test_native_transport_observe_blocks_qq_writes(tmp_path):
    runtime = Runtime(configured(tmp_path))
    with pytest.raises(OneBotError, match="观察"):
        await runtime.bot.call_api("set_group_ban", group_id=102, user_id=101)
    await runtime.close()


def test_saved_tool_switch_stops_tool_execution_without_model(tmp_path):
    runtime = Runtime(configured(tmp_path), bot=FakeBot(), model_client=FakeModel())
    with TestClient(create_app(runtime=runtime)) as client:
        assert client.put('/api/tools/expression_send/enabled',json={'enabled':False}).status_code == 200
        result = client.post('/api/tools/run',json={'session_key':'group:102','name':'expression_send','arguments':{'selector':'表情'}}).json()
        assert result['status'] == 'disabled'
        assert not runtime.chat.model_client.calls and not runtime.bot.sent
        assert client.put('/api/tools/missing/enabled',json={'enabled':False}).status_code == 404


def test_console_sessions_and_events_show_full_identity_and_newest_first(tmp_path, monkeypatch):
    from tangtang_harness.analytics import Analytics

    def full_statistics_not_needed(*args, **kwargs):
        raise AssertionError('会话身份不应读取全量分析和上下文统计')

    monkeypatch.setattr(Analytics, 'sessions', full_statistics_not_needed)
    runtime = Runtime(configured(tmp_path), bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102, group_name='合成群的完整名称')
    runtime.tools.domains.set_alias(102, '短名')
    incoming = InboundEvent('incoming', 103, 101, 102, '成员消息', timestamp=100,
                            sender={'nickname': '合成昵称'})
    own = InboundEvent('own', 103, 103, 102, '机器人回声消息', timestamp=102,
                      sender={'nickname': '合成机器人'})
    private = InboundEvent('private', 103, 101, None, '私聊消息', timestamp=104,
                          sender={'nickname': '合成昵称'})
    for value in (incoming, own, private):
        runtime.store.append_event(value)
    runtime.store.add_tool_result(incoming, ToolCall('ranking', {}), ToolResult('ok', '本地结果'))
    with TestClient(create_app(runtime=runtime)) as client:
        sessions = {row['key']: row for row in client.get('/api/sessions').json()['items']}
        assert sessions['group:102']['title'] == '合成群的完整名称'
        assert sessions['group:102']['alias'] == '短名'
        assert sessions['private:101']['user_nickname'] == '合成昵称'
        records = client.get('/api/sessions/group%3A102/events').json()['items']
        assert [row['at'] for row in records] == sorted([row['at'] for row in records], reverse=True)
        message = next(row for row in records if row['text'] == '机器人回声消息')
        assert message['role'] == 'assistant' and message['outgoing'] is True
        message = next(row for row in records if row['text'] == '成员消息')
        assert message['speaker_nickname'] == '合成昵称' and message['role'] == 'user'
        result = next(row for row in records if row['role'] == 'tool')
        assert result['tool_label'] == '发言排行' and result['speaker_nickname'] == '本地工具：发言排行'
        runtime.tools.domains.ensure_group(102, group_name='更新后的合成群全名')
        updated = {row['key']: row for row in client.get('/api/sessions').json()['items']}
        assert updated['group:102']['title'] == '更新后的合成群全名'
        assert all(row['group_name'] == '更新后的合成群全名'
                   for row in client.get('/api/sessions/group%3A102/events').json()['items'])
