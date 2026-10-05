import asyncio
import json
from dataclasses import replace

import pytest

from tangtang_harness.core_protocol import game_prefix
from tangtang_harness.external import CoreBridge, core_packet
from tangtang_harness.onebot import parse_event
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import ToolCall
from test_runtime_and_console import FakeBot, FakeModel, configured, packet


@pytest.mark.parametrize(("command", "feature"), [
    ("#nte帮助", "nte"), ("#ww帮助", "ww"), ("# WW 帮助", "ww"),
    ("NTE帮助", "nte"), ("wwbot帮助", "ww"), ("#NTEbot帮助", "nte"),
    ("#ntetest", None), ("#wwwhatever", None), ("#yh帮助", None),
    ("请帮我查询ww排行", None),
])
def test_only_upstream_game_prefixes_are_recognized(command, feature):
    assert game_prefix(command) == feature


def test_core_command_after_mention_and_quote_preserves_quote_media():
    event = parse_event({**packet(), "message": [
        {"type": "reply", "data": {"id": "700"}},
        {"type": "at", "data": {"qq": "103"}},
        {"type": "text", "data": {"text": "  # WW 帮助"}},
        {"type": "text", "data": {"text": " 后续参数#保留"}},
    ]})
    event = replace(event, quoted={"text": "原图说明", "message": [
        {"type": "image", "data": {"url": "https://example.invalid/card.png"}},
    ]})
    content = core_packet(event)["content"]
    assert content == [
        {"type": "reply_id", "data": "700"},
        {"type": "reply", "data": "原图说明"},
        {"type": "image", "data": "https://example.invalid/card.png"},
        {"type": "at", "data": "103"},
        {"type": "text", "data": "WW 帮助"},
        {"type": "text", "data": " 后续参数#保留"},
    ]


class CoreSocket:
    def __init__(self):
        self.sent = []

    async def send(self, frame):
        assert isinstance(frame, bytes)
        self.sent.append(json.loads(frame))


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["#nte帮助", "#ww帮助", "# WW 帮助", "ntebot帮助"])
async def test_daily_game_commands_forward_without_parallel_test_prefix(tmp_path, command):
    config = configured(tmp_path, "live", group_ids=(102,), extra={
        "isolated_scope_enabled": True, "test_prefix": "",
        "core": {"enabled": True},
    })
    runtime = Runtime(config, bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    socket = CoreSocket()
    runtime.core.socket = socket
    received = await runtime.receive(packet(command))
    await asyncio.gather(*tuple(runtime.tasks))
    assert received == {"status": "accepted", "route": "tool"}
    assert len(socket.sent) == 1
    assert socket.sent[0]["content"][0]["data"] == command.lstrip().removeprefix("#").lstrip()
    assert not runtime.bot.sent and not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_core_forward_in_observe_sends_nothing_even_with_connected_socket(tmp_path):
    runtime = Runtime(configured(tmp_path, extra={"core": {"enabled": True}}),
                      bot=FakeBot(), model_client=FakeModel())
    socket = CoreSocket()
    bridge = CoreBridge(runtime)
    bridge.socket = socket
    with pytest.raises(RuntimeError, match="Core 连接尚未就绪"):
        await bridge.forward(parse_event(packet("#ww帮助")))
    assert not socket.sent
    assert (await runtime.receive(packet("#ww帮助")))["status"] == "observed"
    assert not runtime.bot.sent and not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_game_feature_and_private_boundaries_remain_independent_of_mini_games(tmp_path):
    config = configured(tmp_path, "live", group_ids=(102,), extra={
        "isolated_scope_enabled": True, "test_prefix": "", "core": {"enabled": True},
    })
    runtime = Runtime(config, bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    runtime.tools.domains.set_feature(102, "mini_games", False)
    event = parse_event(packet("#ww帮助"))
    assert runtime.game_allowed(event)
    assert not runtime.game_allowed(replace(event, group_id=None))
    runtime.tools.domains.set_feature(102, "ww", False)
    assert not runtime.game_allowed(event)
    assert runtime.game_allowed(replace(event, text="#nte帮助"))
    result = await runtime.execute_call(event, ToolCall("external_game", {"text": event.text}))
    assert result.status == "disabled"
    assert not runtime.chat.model_client.calls
    await runtime.close()


def core_runtime(tmp_path):
    config = configured(tmp_path, "live", group_ids=(102,), extra={
        "isolated_scope_enabled": True, "test_prefix": "", "core": {"enabled": True},
    })
    runtime = Runtime(config, bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["#nte帮助", "nte帮助", "#ww帮助", "ww帮助"])
@pytest.mark.parametrize("disabled_by", ["group_feature", "global", "core", "tool", "tool_scope", "private"])
async def test_unavailable_game_commands_remain_silent(tmp_path, command, disabled_by):
    runtime = core_runtime(tmp_path)
    socket = CoreSocket()
    runtime.core.socket = socket
    incoming = packet(command)
    if disabled_by == "group_feature":
        runtime.tools.domains.set_feature(102, game_prefix(command), False)
    elif disabled_by == "global":
        runtime.store.set_setting("game_api_enabled", False)
    elif disabled_by == "core":
        runtime.config = replace(runtime.config, extra={**runtime.config.extra, "core": {"enabled": False}})
    elif disabled_by == "tool":
        runtime.store.set_setting("tool_enabled:external_game", False)
    elif disabled_by == "tool_scope":
        runtime.store.set_setting("tool_groups:external_game", [104])
    else:
        incoming = {**incoming, "message_type": "private"}
        incoming.pop("group_id")
    try:
        assert await runtime.receive(incoming) == {"status": "accepted", "route": "tool"}
        await asyncio.gather(*tuple(runtime.tasks))
        assert not runtime.bot.sent
        assert not socket.sent and not runtime.core_sources
        assert not runtime.chat.model_client.calls
        session_key = parse_event(incoming).session_key
        result = runtime.store.tool_results(session_key)[0]["result"]
        assert result["status"] == "disabled" and not result["text"] and not result["messages"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_core_subscription_response_survives_harness_restart(tmp_path):
    runtime = core_runtime(tmp_path)
    source = parse_event(packet("#nte订阅公告", 55))
    runtime.store.append_event(source)
    assert not runtime.core_sources
    await runtime.core.receive({"bot_self_id": "103", "msg_id": "55", "target_type": "group", "target_id": "102",
                                "content": [{"type": "text", "data": "上游公告"}]})
    assert len(runtime.bot.sent) == 1
    assert runtime.bot.sent[0][0].event_id == "55"
    assert not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_core_legacy_subscription_route_retains_its_feature_and_actor(tmp_path):
    runtime = core_runtime(tmp_path)
    source = parse_event(packet("#nte", 66))
    runtime.store.set_setting("core_subscription_sources", [source.to_dict()])
    response = {"bot_self_id": "103", "msg_id": "66", "target_type": "group", "target_id": "102",
                "content": [{"type": "text", "data": "签到结果"}]}
    await runtime.core.receive(response)
    assert runtime.bot.sent[0][0].user_id == 101
    runtime.tools.domains.set_feature(102, "nte", False)
    await runtime.core.receive(response)
    assert len(runtime.bot.sent) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_core_broadcast_from_primary_and_legacy_connections_delivers_once_and_receipts_both(tmp_path):
    runtime = core_runtime(tmp_path)
    primary, legacy = CoreSocket(), CoreSocket()
    response = {"bot_id": "onebot", "bot_self_id": "", "msg_id": "", "target_type": "group", "target_id": "102",
                "content": [{"type": "text", "data": "鸣潮订阅通知"}]}
    await asyncio.gather(runtime.core.receive({**response, "echo": "p1"}, socket=primary),
                         runtime.core.receive({**response, "echo": "l1"}, socket=legacy))
    assert len(runtime.bot.sent) == 1
    assert primary.sent[0]["content"][0]["data"] == {"echo": "p1", "id": "1001"}
    assert legacy.sent[0]["content"][0]["data"] == {"echo": "l1", "id": "1001"}
    assert not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_core_login_can_send_private_result_for_linked_group_request(tmp_path):
    runtime = core_runtime(tmp_path)
    event = parse_event(packet("#nte登录", 77))
    runtime.core_sources[event.event_id] = event
    await runtime.core.receive({"bot_self_id": "103", "msg_id": "77", "target_type": "direct", "target_id": "101",
                                "content": [{"type": "text", "data": "登录结果"}]})
    delivered = runtime.bot.sent[0][0]
    assert delivered.group_id is None and delivered.user_id == 101
    assert not runtime.chat.model_client.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_unknown_core_push_obeys_scope_game_and_observe_switches(tmp_path):
    runtime = core_runtime(tmp_path)
    response = {"bot_self_id": "103", "msg_id": "old-message", "target_type": "group", "target_id": "104",
                "content": [{"type": "text", "data": "上游通知"}]}
    await runtime.core.receive(response)
    runtime.tools.domains.set_feature(102, "nte", False)
    runtime.tools.domains.set_feature(102, "ww", False)
    await runtime.core.receive({**response, "target_id": "102"})
    runtime.config = replace(runtime.config, mode="observe")
    await runtime.core.receive({**response, "target_id": "102"})
    assert not runtime.bot.sent
    await runtime.close()


@pytest.mark.asyncio
async def test_imported_subscription_cannot_send_outside_current_production_scope(tmp_path):
    runtime = core_runtime(tmp_path)
    runtime.tools.domains.ensure_group(104)
    source = replace(parse_event(packet("#nte", 88)), group_id=104)
    runtime.store.set_setting("core_subscription_sources", [source.to_dict()])
    await runtime.core.receive({"bot_self_id": "103", "msg_id": "88", "target_type": "group", "target_id": "104",
                                "content": [{"type": "text", "data": "订阅推送"}]})
    assert not runtime.bot.sent
    await runtime.close()


@pytest.mark.asyncio
async def test_explicit_parallel_game_reply_uses_original_message_scope(tmp_path):
    runtime = Runtime(configured(tmp_path, "live", extra={"core": {"enabled": True}}),
                      bot=FakeBot(), model_client=FakeModel())
    runtime.tools.domains.ensure_group(102)
    original = parse_event(packet("#harness #nte帮助", 99))
    runtime.store.append_event(original)
    source = replace(original, text="#nte帮助")
    runtime.core_sources["99"] = source
    await runtime.core.receive({"bot_self_id": "103", "msg_id": "99", "target_type": "group", "target_id": "102",
                                "content": [{"type": "text", "data": "独立测试结果"}]})
    assert len(runtime.bot.sent) == 1
    await runtime.close()


@pytest.mark.asyncio
async def test_legacy_receiver_never_forwards_requests_and_stops_with_bridge(tmp_path):
    from websockets.asyncio.server import serve

    runtime = core_runtime(tmp_path)
    connected = asyncio.Event()
    sockets, inbound = {}, []

    async def server(socket):
        identity = socket.request.path.rsplit("/", 1)[-1]
        sockets[identity] = socket
        if len(sockets) == 2:
            connected.set()
        async for raw in socket:
            inbound.append((identity, json.loads(raw)))

    async with serve(server, "127.0.0.1", 0) as service:
        port = service.sockets[0].getsockname()[1]
        runtime.config = replace(runtime.config, extra={**runtime.config.extra, "core": {
            "enabled": True, "url": f"ws://127.0.0.1:{port}",
            "identity": "HarnessPrimary", "receive_identities": ["LegacyGameReceiver"],
        }})
        runtime.core.start()
        await asyncio.wait_for(connected.wait(), 3)
        async with asyncio.timeout(3):
            while runtime.core.socket is None:
                await asyncio.sleep(.01)
        event = parse_event(packet("#ww帮助"))
        await runtime.core.forward(event)
        async with asyncio.timeout(3):
            while not inbound:
                await asyncio.sleep(.01)
        assert [identity for identity, _ in inbound] == ["HarnessPrimary"]
        await runtime.core.close()
        assert not runtime.core.receive_tasks and runtime.core.socket is None
    await runtime.close()
