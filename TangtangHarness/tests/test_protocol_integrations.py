import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tangtang_harness.app import create_app
from tangtang_harness.external import CoreBridge, SpeechClient, core_packet
from tangtang_harness.onebot import NativeBot, OneBotError, parse_event
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import ToolCall, ToolResult
from test_runtime_and_console import configured, packet, FakeBot, FakeModel


class Socket:
    def __init__(self):
        self.frames = asyncio.Queue()
        self.sent = []

    async def send_json(self, frame):
        await self.frames.put(frame)

    async def send(self, frame):
        self.sent.append(json.loads(frame))


@pytest.mark.asyncio
async def test_rpc_receipts_failure_disconnect_and_reconnect():
    bot, first = NativeBot(send_interval=0, timeout=.1), Socket()
    await bot.attach(first, 103)
    event = parse_event(packet())
    request = asyncio.create_task(bot.send(event, "消息"))
    action = await first.frames.get()
    assert action["action"] == "send_group_msg"
    assert action["params"]["group_id"] == 102
    bot.receive_response({"status": "ok", "retcode": 0, "echo": action["echo"], "data": {"message_id": 701}})
    assert await request == {"message_id": 701}
    request = asyncio.create_task(bot.send(event, "失败"))
    action = await first.frames.get()
    bot.receive_response({"status": "failed", "retcode": 100, "echo": action["echo"], "wording": "平台失败"})
    with pytest.raises(OneBotError, match="平台失败"):
        await request
    request = asyncio.create_task(bot.call_api("get_group_list"))
    await first.frames.get()
    bot.detach(first)
    with pytest.raises(OneBotError, match="disconnected"):
        await request
    assert not bot.pending
    second = Socket()
    await bot.attach(second, 103)
    bot.detach(first)
    assert bot.connected
    request = asyncio.create_task(bot.send(event, "无回执"))
    action = await second.frames.get()
    bot.receive_response({"status": "ok", "echo": action["echo"], "data": {}})
    with pytest.raises(OneBotError, match="message_id"):
        await request
    bot.detach(second)


def test_reverse_websocket_does_not_block_rpc_reader(tmp_path):
    runtime = Runtime(configured(tmp_path, "live"), model_client=FakeModel())
    runtime.bot.send_interval = 0
    async def result(event, call):
        return ToolResult("ok", "本地结果")
    runtime.execute_call = result
    with TestClient(create_app(runtime=runtime)) as client:
        with client.websocket_connect("/onebot/v11/ws", headers={"x-self-id": "103"}) as socket:
            action = socket.receive_json()
            assert action["action"] == "get_group_list"
            socket.send_json({"status": "ok", "retcode": 0, "echo": action["echo"],
                              "data": [{"group_id": 102, "group_name": "合成群"}]})
            socket.send_json(packet("#harness 今天发言排行"))
            action = socket.receive_json()
            assert action["action"] == "send_group_msg"
            socket.send_json({"status": "ok", "retcode": 0, "echo": action["echo"], "data": {"message_id": 700}})
            with pytest.raises(WebSocketDisconnect) as duplicate:
                with client.websocket_connect("/onebot/v11/ws"):
                    pass
            assert duplicate.value.code == 1013
            assert client.get("/api/status").json()["transport"]["connected"]
        assert runtime.store.tool_results("group:102")
    assert not runtime.chat.model_client.calls


@pytest.mark.asyncio
async def test_core_identity_order_nodes_files_receipts_and_scope(tmp_path):
    event = parse_event(packet("#nte 帮助"))
    frames, deliveries = [], []
    class Bot:
        self_id = 103
        async def call_api(self, action, **params):
            frames.append((action, params))
            return {"message_id": 800}
    async def deliver(event, segments, **kwargs):
        deliveries.append(segments)
        return [str(900 + len(deliveries))]
    async def deliver_forward(event, nodes, **kwargs):
        result = await runtime.bot.call_api('send_group_forward_msg', group_id=event.group_id,messages=nodes)
        return [str(result['message_id'])]
    runtime = SimpleNamespace(config=SimpleNamespace(mode="live", root=tmp_path, extra={"core": {"enabled": True}}),
        bot=Bot(), core_sources={event.event_id: event}, game_allowed=lambda _: True, deliver=deliver, deliver_forward=deliver_forward,
        group_delivery_allowed=lambda _: True,
        in_scope=lambda item: item.group_id == 102,
        store=SimpleNamespace(get_setting=lambda key, default=None: [101] if key == "operator_ids" else default,
                              event=lambda _: None))
    bridge, socket = CoreBridge(runtime), Socket()
    bridge.socket = socket
    await bridge.forward(event)
    sent = socket.sent[0]
    assert sent["bot_id"] == "onebot" and sent["bot_self_id"] == "103" and sent["user_pm"] == 1
    assert sent["content"][0]["data"] == "nte 帮助"
    content = [{"type": "text", "data": "第一条"},
        {"type": "node", "data": [{"type": "text", "data": "转发"}, {"type": "image", "data": "YWJj"}]},
        {"type": "file", "data": "example.txt|" + base64.b64encode(b"content").decode()},
        {"type": "text", "data": "最后一条"}]
    response = {"bot_self_id": "103", "msg_id": event.event_id, "target_type": "group", "target_id": "102", "content": content, "echo": "r1"}
    await bridge.receive(response)
    assert len(deliveries) == 2
    assert frames[0][0] == "send_group_forward_msg"
    assert frames[0][1]["messages"][1]["data"]["content"][0]["data"]["file"] == "base64://YWJj"
    assert frames[1][0] == "upload_group_file"
    file = Path(frames[1][1]["file"])
    assert file.is_relative_to(tmp_path) and file.read_bytes() == b"content"
    recall = socket.sent[-1]["content"][0]["data"]
    assert recall == {"echo": "r1", "id": ["901", "800", "902"]}
    await bridge.receive({**response, "target_type": "direct"})
    await bridge.receive({**response, "bot_self_id": "other-bot"})
    await bridge.receive({**response, "msg_id": "unknown", "target_id": "104"})
    runtime.game_allowed = lambda _: False
    await bridge.receive(response)
    assert len(deliveries) == 2 and len(frames) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ('file', 'excute_delete_message', 'excute_ban_user'))
async def test_core_direct_actions_recheck_membership_after_await(tmp_path, kind):
    runtime = Runtime(configured(tmp_path, 'live', group_ids=(102,), extra={
        'isolated_scope_enabled': True, 'test_prefix': '', 'core': {'enabled': True}}),
        bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    event = parse_event(packet('#nte 帮助'))
    runtime.core_sources[event.event_id] = event
    frames = []

    async def action(name, **params):
        frames.append((name, params))
        return {'message_id': 800}

    async def leave_after_flush(*args, **kwargs):
        runtime.tools.domains.disable_group(102)
        return ['700']

    async def leave_after_download(*args):
        runtime.tools.domains.disable_group(102)
        return tmp_path / 'synthetic.txt'

    runtime.bot.call_api = action
    runtime.deliver = leave_after_flush
    runtime.core.materialize_file = leave_after_download
    data = 'synthetic.txt|YWJj' if kind == 'file' else {'message_id': '1', 'user_id': 101, 'duration': 30}
    content = ([{'type': 'text', 'data': '先发送文本'}] if kind != 'file' else []) + [{'type': kind, 'data': data}]
    try:
        await runtime.core.receive({'bot_self_id': '103', 'msg_id': event.event_id,
            'target_type': 'group', 'target_id': '102', 'content': content})
        assert frames == []
    finally:
        await runtime.close()


@pytest.mark.parametrize("command", ["#nte 帮助", "#ww 帮助"])
def test_core_packet_preserves_game_command_semantics(command):
    event = parse_event(packet(command))
    payload = core_packet(event)
    assert payload["content"][0] == {"type": "text", "data": command[1:]}


@pytest.mark.asyncio
async def test_core_unavailable_does_not_block_local_runtime_path(tmp_path):
    runtime = Runtime(configured(tmp_path, "live"), bot=FakeBot(), model_client=FakeModel())
    event = parse_event(packet("#harness 今天发言排行"))
    bridge = CoreBridge(runtime)

    with pytest.raises(RuntimeError, match="Core 连接尚未就绪"):
        await bridge.forward(event)

    result = await runtime.execute_call(event, ToolCall("user_help", {"text": "#帮助"}))
    assert result.status == "ok"
    assert not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_speech_uses_only_independent_endpoint_and_cached_queue(tmp_path, monkeypatch):
    requests = []
    def answer(request):
        requests.append(request)
        import io, wave
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b"\0\0" * 80)
        return httpx.Response(200, content=output.getvalue())
    client_class = httpx.AsyncClient
    monkeypatch.setattr("tangtang_harness.external.httpx.AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(answer), **kwargs))
    speech = SpeechClient(tmp_path)
    settings = {"endpoint": "http://127.0.0.1:9890", "ref_audio_path": "reference.wav", "text_lang": "all_zh",
                "prompt_text": "参考内容", "prompt_lang": "zh", "speed_factor": .85, "temperature": .7}
    await speech.check(settings)
    one, two = await asyncio.gather(speech.synthesize("你好", settings), speech.synthesize("你好", settings))
    assert one == two and one.is_relative_to(tmp_path)
    assert [r.url.path for r in requests] == ["/docs", "/tts"]
    assert all(r.url.port == 9890 for r in requests)
    payload = json.loads(requests[-1].content)
    assert payload["text_lang"] == "all_zh" and payload["speed_factor"] == .85
    assert payload["temperature"] == .7 and payload["prompt_text"] == "参考内容"
    assert not any("weight" in r.url.path or "supervisor" in r.url.path for r in requests)


@pytest.mark.asyncio
async def test_chat_management_preserves_request_intent_and_transport_history(tmp_path):
    runtime = Runtime(configured(tmp_path, "live"), bot=FakeBot(), model_client=FakeModel())
    runtime.store.set_setting("operator_ids", [101])
    event = parse_event(packet())
    result = await runtime.execute_call(event, ToolCall("model_settings", {"text": "#糖糖模型 样例模型"}))
    assert result.status == "ok" and runtime.config.active_model == "sample"
    await runtime.execute_call(event, ToolCall("proactive_settings", {"text": "#糖糖主动回复 开"}))
    assert runtime.config.proactive_enabled
    await runtime.execute_call(event, ToolCall("proactive_settings", {"text": "#糖糖主动回复 概率 0.3"}))
    assert runtime.config.extra["proactive_probability"] == .3
    runtime.transport_disconnected()
    runtime.transport_connected()
    result = await runtime.execute_call(event, ToolCall("qq_transport_status"))
    assert len(result.data["incidents"]) == 1 and result.data["incidents"][0]["ended_at"]
    await runtime.close()


@pytest.mark.asyncio
async def test_forward_help_records_receipt_without_chat(tmp_path):
    class ForwardBot(FakeBot):
        async def call_api(self, action, **params):
            assert action == 'send_group_forward_msg'
            self.sent.append(params)
            return {'message_id':700}
    runtime = Runtime(configured(tmp_path,'live'),bot=ForwardBot(),model_client=FakeModel())
    event = parse_event(packet())
    nodes = [{'type':'node','data':{'uin':'103','name':'帮助','content':[{'type':'text','data':{'text':'完整帮助'}}]}}]
    await runtime.deliver_tool(event, ToolResult('ok','折叠文字',{'forward_nodes':nodes}))
    with runtime.store.connect() as conn:
        assert json.loads(conn.execute("SELECT message_ids FROM deliveries WHERE outcome='delivered'").fetchone()[0]) == ['700']
    assert len(runtime.bot.sent) == 1 and not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_private_background_notification_uses_scoped_user_and_confirmation(tmp_path):
    config = configured(tmp_path,'live',extra={'isolated_scope_enabled':True,'private_user_ids':[101]})
    runtime = Runtime(config,bot=FakeBot(),model_client=FakeModel())
    confirmed = asyncio.Event()
    async def tick(*, group_ids, user_ids):
        assert user_ids == [101]
        if confirmed.is_set():
            return []
        return [{'group_id':None,'user_id':101,'self_id':103,'result':ToolResult('ok','登录完成')}]
    def mark(result, success):
        assert success and runtime.bot.sent[-1][0].group_id is None
        assert runtime.bot.sent[-1][0].user_id == 101
        confirmed.set()
    runtime.tools.tick, runtime.tools.mark_delivered = tick, mark
    worker = asyncio.create_task(runtime._scheduler())
    await asyncio.wait_for(confirmed.wait(),2)
    worker.cancel()
    await asyncio.gather(worker,return_exceptions=True)
    assert len(runtime.bot.sent) == 1 and not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_chat_switches_preserve_local_execution_and_group_state(tmp_path):
    runtime = Runtime(configured(tmp_path,'live'),bot=FakeBot(),model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    event = parse_event(packet())
    assert runtime.chat_allowed(event)
    runtime.tools.domains.set_feature(102,'mention_chat',False)
    assert not runtime.chat_allowed(event)
    runtime.store.set_setting('operator_ids',[101])
    await runtime.execute_call(event,ToolCall('chat_settings',{'text':'#系统设置 被呼叫会话 关'}))
    await runtime.execute_call(event,ToolCall('chat_settings',{'text':'#系统设置 被呼叫会话 开'}))
    assert not runtime.chat_allowed(event)
    runtime.tools.domains.set_feature(102,'mention_chat',True)
    assert runtime.chat_allowed(event)
    assert not runtime.chat.model_client.calls
    await runtime.close()
