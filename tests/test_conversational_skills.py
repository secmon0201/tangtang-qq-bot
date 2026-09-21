"""Exercise real dialogue-to-skill paths with captured QQ delivery only."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from tests.test_tangtang_chat import enabled_config, group_message, make_service, usage_events
from bot.application.chat_continuation import MergedEvent
from bot.application import local_features
from bot.plugins import tangtang_chat as plugin
from bot.plugins import commands, asoul, today_wife
from bot.services.local_skill_contract import FeatureRequest, ACTION_CONTRACTS
from bot.services.skill_audit import SkillAuditLedger
from bot.services.skill_metrics import SkillMetricsStore
from bot.services.skills import local_action_skill


class CaptureBot:
    self_id = 2

    def __init__(self):
        self.sent = []

    async def send(self, event, message, **kwargs):
        self.sent.append((event, Message(message)))
        return {"message_id": len(self.sent)}


@pytest.fixture
def rig(monkeypatch, tmp_path):
    service, _, provider, usage = make_service(tmp_path, monkeypatch)
    config = enabled_config(TANGTANG_CALL_KEYWORD="娅娅")
    state = SimpleNamespace(disabled=set(), available=True, game=True)
    domain = SimpleNamespace(mode="solo", name="测试群", alias="", domain_id=7, public_token="A" * 43)
    domains = SimpleNamespace(domain_for_group=lambda _: domain,
        effective_feature_enabled=lambda group, key: key not in state.disabled)
    monkeypatch.setattr(plugin, "group_domains", lambda: domains)
    monkeypatch.setattr(plugin, "passive_settings", lambda: SimpleNamespace(is_game_api_enabled=lambda: state.game))
    monkeypatch.setattr(plugin, "settings", SimpleNamespace(game_api_enabled=True))
    monkeypatch.setattr(plugin, "is_super_admin", lambda _: False)
    monkeypatch.setattr(plugin, "capabilities", SimpleNamespace(skill_available=lambda _: state.available))
    monkeypatch.setattr(plugin, "skill_ledger", SkillAuditLedger(tmp_path / "audit.db"))
    monkeypatch.setattr(plugin, "skill_metrics", SkillMetricsStore(tmp_path / "metrics.db"))
    monkeypatch.setattr(plugin, "service", service)
    service.feature_runner, service.feature_catalog = plugin._run_skill_requests, plugin._available_model_skills
    invoked = []

    async def handler(matcher, bot, event, request):
        invoked.append((event, request))
        await matcher.finish(MessageSegment.image("base64://AA=="))

    monkeypatch.setattr(local_features, "_handlers", {action: handler for action in ACTION_CONTRACTS})
    return SimpleNamespace(service=service, provider=provider, config=config, state=state,
        domains=domains, domain=domain, bot=CaptureBot(), invoked=invoked, usage=usage)


@pytest.mark.parametrize("text,expected", [
    ("娅娅，那你告诉我这周谁最能聊，顺便我还要看看这周直播什么", [("ranking", "周"), ("week_live", "")]),
    ("娅娅，先看今天的发言排行，然后看本周直播", [("ranking", "日"), ("week_live", "")]),
    ("娅娅我要看发言排行", [("ranking", "日")]),
    ("娅娅看看他本月发言统计", [("ranking", "月")]),
    ("娅娅看看我的今日缘分", [("wife_personal", "")]),
    ("娅娅看看本群缘分", [("wife_group", "")]),
    ("娅娅，要看美图", [("denia_gallery", "")]),
    ("娅娅，要一个娅娅的美图", [("denia_gallery", "")]),
    ("娅娅，想看你的自拍", [("denia_gallery", "")]),
    ("娅娅，发一张照片", [("denia_gallery", "")]),
    ("娅娅，来到好看的照片", [("denia_gallery", "")]),
    ("娅娅，来张好看的照片", [("denia_gallery", "")]),
    ("娅娅，给我看看你的美照", [("denia_gallery", "")]),
])
def test_natural_requests_preserve_merged_group_identity(rig, text, expected):
    original = group_message(group_id=1001, text=text)
    handled, usage = asyncio.run(plugin._feature_router(rig.bot, MergedEvent([original]), rig.config, text))
    assert handled and usage == {} and rig.provider.calls == 0
    assert [(r.action, r.args) for _, r in rig.invoked] == expected
    assert all(event is original for event, _ in rig.invoked)
    assert all(event is original for event, _ in rig.bot.sent)
    assert sum(any(s.type == "image" for s in m) for _, m in rig.bot.sent) == len(expected)
    assert "糖糖" not in rig.bot.sent[0][1].extract_plain_text()
    rows = rig.service.db.list_calls(3, 1001)
    assert len(rows) == len(expected)
    assert all(row["reply_text"] == "[功能图片]" for row in rows)


def test_real_ranking_and_live_handlers_receive_original_event(rig, monkeypatch, tmp_path):
    from bot.plugins import commands, asoul
    monkeypatch.setitem(local_features._handlers, "ranking", commands._run_local_ranking_feature)
    monkeypatch.setitem(local_features._handlers, "week_live", asoul._run_local_live_feature)
    rig.domains.public_group_key = lambda _: "test-key"
    monkeypatch.setattr(commands, "group_domains", lambda: rig.domains)
    queried = []

    def rows(scope, groups):
        queried.append((scope, groups))
        return []

    async def payload(*args, **kwargs):
        assert kwargs["selected_group_id"] == 1001
        return {"title": "今日发言榜", "subtitle": "测试"}

    async def render(_):
        path = tmp_path / "ranking.png"
        path.write_bytes(b"test-image")
        return path

    async def week_reply(matcher, args):
        await matcher.finish(MessageSegment.image("base64://AA=="))

    monkeypatch.setattr(commands, "stats_service", SimpleNamespace(ranking_rows_for_groups=rows))
    monkeypatch.setattr(commands, "build_community_ranking_payload", payload)
    monkeypatch.setattr(commands.community_web_renderer, "render_ranking", render)
    monkeypatch.setattr(asoul, "finish_week_schedule_reply", week_reply)
    text = "娅娅，先看今天的发言排行，然后看本周直播"
    event = MergedEvent([group_message(group_id=1001, text=text)])
    asyncio.run(plugin._feature_router(rig.bot, event, rig.config, text))
    assert queried == [("day", (1001,))]
    assert sum(any(s.type == "image" for s in m) for _, m in rig.bot.sent) == 2
    assert all("目标 QQ 群" not in str(m) for _, m in rig.bot.sent)


@pytest.mark.parametrize("path", ["single", "plan", "model"])
@pytest.mark.parametrize("gate", ["rollout", "feature", "role", "capability", "handler"])
def test_every_execution_path_enforces_gates_before_opener(rig, monkeypatch, path, gate):
    if gate == "rollout":
        plugin.skill_ledger.set_control("commands", enabled=False)
    elif gate == "feature":
        rig.state.disabled.add("speech_ranking")
    elif gate == "role":
        original_lookup = local_action_skill
        monkeypatch.setattr(plugin, "local_action_skill", lambda action: replace(original_lookup(action), required_role="admin"))
    elif gate == "capability":
        rig.state.available = False
    else:
        local_features._handlers.pop("ranking")
    text = "娅娅看今天发言排行" + ("，然后看本周直播" if path == "plan" else "")
    event = group_message(group_id=1001, text=text)
    if path == "model":
        rig.provider.response = json.dumps({"decision": "reply", "messages": ["准备看看"],
            "feature_calls": [{"action": "ranking", "args": "日"}]})
        asyncio.run(rig.service._model_reply(rig.bot, event, rig.config, call_text=text))
    else:
        asyncio.run(plugin._feature_router(rig.bot, event, rig.config, text))
    assert not rig.invoked
    assert len(rig.bot.sent) == 1
    assert "准备看看" not in str(rig.bot.sent[0][1])


def test_model_batch_executes_and_records_one_usage_charge(rig):
    text = "娅娅，把这礼拜大家聊天的名次和直播安排给我瞧瞧"
    rig.provider.response = json.dumps({"decision": "reply", "messages": [], "voice": "text",
        "feature_calls": [{"action": "ranking", "args": "周"}, {"action": "week_live"}]})
    event = MergedEvent([group_message(group_id=1001, text=text)])
    asyncio.run(rig.service._model_reply(rig.bot, event, rig.config, call_text=text))
    assert [r.action for _, r in rig.invoked] == ["ranking", "week_live"]
    assert len(rig.bot.sent) == 2
    assert all(any(s.type == "image" for s in m) for _, m in rig.bot.sent)
    assert usage_events(rig.usage)[-1]["event"] == "feature"
    assert plugin.skill_metrics.summary()["calls"] == 2


def test_native_tool_batch_uses_same_handlers_and_ordered_preflight(rig):
    text = "娅娅，先看本群这周发言排行，再看本周直播"
    rig.provider.tool_sequence = [("", [
        {"call_id": "n1", "name": "ranking",
         "arguments": '{"period":"周","scope":"group"}'},
        {"call_id": "n2", "name": "week_live", "arguments": "{}"},
    ])]
    config = replace(rig.config, native_action_tools="true")
    event = MergedEvent([group_message(group_id=1001, text=text)])
    asyncio.run(rig.service._model_reply(rig.bot, event, config, call_text=text))
    assert [(request.action, request.args) for _, request in rig.invoked] == [
        ("ranking", "周"), ("week_live", "")
    ]
    assert len(rig.bot.sent) == 2
    assert rig.provider.calls == 1
    with rig.service.db._connect() as conn:
        rows = conn.execute(
            "SELECT item_type, delivery_status FROM chat_context_turns ORDER BY id"
        ).fetchall()
    assert [row["item_type"] for row in rows] == [
        "message", "tool_call", "tool_call", "tool_result", "tool_result"
    ]
    assert all(row["delivery_status"] == "confirmed" for row in rows)


def test_native_tool_batch_denial_executes_nothing(rig):
    text = "娅娅，先看本群这周发言排行，再看本周直播"
    rig.state.disabled.add("speech_ranking")
    rig.provider.tool_sequence = [("", [
        {"call_id": "n1", "name": "ranking",
         "arguments": '{"period":"周","scope":"group"}'},
        {"call_id": "n2", "name": "week_live", "arguments": "{}"},
    ])]
    event = group_message(group_id=1001, text=text)
    asyncio.run(rig.service._model_reply(
        rig.bot, event, replace(rig.config, native_action_tools="true"), call_text=text
    ))
    assert not rig.invoked
    assert len(rig.bot.sent) == 1
    assert rig.provider.calls == 1


def test_model_can_request_one_random_denia_gallery_image(rig):
    text = "娅娅，来点好看的"
    rig.provider.response = json.dumps(
        {
            "decision": "reply",
            "messages": [],
            "voice": "text",
            "feature_call": {"action": "denia_gallery"},
        }
    )
    event = MergedEvent([group_message(group_id=1001, text=text)])

    asyncio.run(rig.service._model_reply(rig.bot, event, rig.config, call_text=text))

    assert [(request.action, request.args) for _, request in rig.invoked] == [
        ("denia_gallery", "")
    ]
    assert len(rig.bot.sent) == 1
    assert any(segment.type == "image" for segment in rig.bot.sent[0][1])


@pytest.mark.parametrize("change", ["silent", "voice", "cluster", "args", "unknown", "extra", "many", "proactive", "stale"])
def test_invalid_or_inapplicable_model_requests_never_execute(rig, monkeypatch, change):
    payload = {"decision": "reply", "messages": [], "feature_call": {"action": "ranking", "args": "周"}}
    if change == "silent": payload["decision"] = "silent"
    if change == "voice": payload["voice"] = "invalid"
    if change == "cluster": payload["feature_call"]["cluster"] = "false"
    if change == "args": payload["feature_call"]["args"] = {"period": "周"}
    if change == "unknown": payload["feature_call"]["action"] = "archive_search"
    if change == "extra": payload["feature_call"]["group_id"] = 1002
    if change == "many": payload["feature_calls"] = [payload.pop("feature_call")] * 7
    if change == "stale": monkeypatch.setattr(rig.service, "_turn_current", lambda: False)
    rig.provider.response = json.dumps(payload)
    event = group_message(group_id=1001, text="娅娅看本周排行")
    asyncio.run(rig.service._model_reply(rig.bot, event, rig.config, call_text=event.get_plaintext(), proactive=change == "proactive"))
    assert not rig.invoked and not rig.bot.sent


def test_failure_has_feedback_and_is_not_recorded_as_success(rig, monkeypatch):
    async def fail(*args):
        raise RuntimeError("test failure")
    monkeypatch.setitem(local_features._handlers, "ranking", fail)
    event = group_message(group_id=1001, text="娅娅看发言排行")
    asyncio.run(plugin._feature_router(rig.bot, event, rig.config, event.get_plaintext()))
    assert "没查成功" in str(rig.bot.sent[-1][1])
    assert plugin.skill_metrics.summary()["failures"] == 1
    assert "没查成功" in rig.service.db.list_calls(3, 1001)[0]["reply_text"]


def test_catalog_excludes_disabled_and_unregistered_actions(rig):
    rig.state.disabled.update({"speech_ranking", "mini_games", "today_wife", "ww", "nte", "zhijiang_calendar"})
    event = group_message(group_id=1001)
    assert rig.service.feature_catalog(event) == (
        "today_live",
        "tomorrow_live",
        "week_live",
        "denia_gallery",
    )
    prompt = rig.service._build_prompt(event, rig.config)
    assert "聊天记录全文检索" in prompt and "没有开放" in prompt
    assert "- ranking:" not in prompt


@pytest.mark.parametrize("text", ["娅娅先看今天发言排行，然后帮我写个新功能", "娅娅别查发言排行", "娅娅昨天发言排行", "娅娅今天直播好看吗",
    "娅娅，把这礼拜大家聊天的名次和直播安排给我瞧瞧", "娅娅发言排行，直播安排", "娅娅看看鸣潮今汐排行", "娅娅我今天看了直播", "娅娅我的老婆今天好可爱"])
def test_partial_unsupported_or_non_request_reaches_main_model(rig, text):
    handled, _ = asyncio.run(plugin._feature_router(rig.bot, group_message(group_id=1001, text=text), rig.config, text))
    assert not handled and not rig.invoked and not rig.bot.sent


@pytest.mark.parametrize("action,feature", [("nte_rank", "nte"), ("wuwa_rank", "ww"), ("mini_game_dice", "mini_games"), ("wife_personal", "today_wife")])
def test_real_manifest_maps_queries_to_their_group_gates(rig, action, feature):
    rig.state.disabled.add(feature)
    args = ACTION_CONTRACTS[action].args[0]
    event = group_message(group_id=1001)
    assert plugin._feature_denial(event, FeatureRequest(action, args)) == "group_disabled"
    assert action not in plugin._available_model_skills(event)


@pytest.mark.parametrize("action", ["nte_rank", "wuwa_rank"])
def test_game_master_gate_applies_to_conversation(rig, action):
    rig.state.game = False
    assert plugin._feature_denial(group_message(group_id=1001), FeatureRequest(action, "群")) == "group_disabled"


@pytest.mark.parametrize("skill_request", [FeatureRequest("ranking", "总"), FeatureRequest("ranking", "日", True)])
def test_model_cannot_expand_scope_without_request(rig, skill_request):
    rig.domain.mode = "cluster"
    event = group_message(group_id=1001, text="娅娅看发言排行")
    asyncio.run(plugin._run_skill_requests(rig.bot, event, rig.config, (skill_request,), text=event.get_plaintext()))
    assert not rig.invoked and len(rig.bot.sent) == 1


def test_stale_turn_during_render_sends_neither_result_nor_next_step(rig, monkeypatch):
    current = [True]
    async def render_then_stale(matcher, bot, event, request):
        current[0] = False
        await matcher.finish(MessageSegment.image("base64://AA=="))
    monkeypatch.setitem(local_features._handlers, "ranking", render_then_stale)
    event = group_message(group_id=1001)
    asyncio.run(plugin._run_skill_requests(rig.bot, event, rig.config,
        (FeatureRequest("ranking", "日"), FeatureRequest("week_live")), current=lambda: current[0]))
    assert not rig.bot.sent and not rig.invoked


@pytest.mark.parametrize("action", ["wife_personal", "wife_group"])
def test_wife_query_reuses_existing_images_without_draw_or_interaction(rig, monkeypatch, tmp_path, action):
    calls = []
    def personal(group_id, user_id):
        calls.append(("personal", group_id, user_id))
        return {"own": [], "incoming": []}
    def group(group_id):
        calls.append(("group", group_id))
        return {"records": [], "day": "2026-09-19", "day_state": {"script_title": "test"}, "spotlight": ""}
    # No mutation methods exist on this fake: any draw/divorce/interaction fails.
    monkeypatch.setattr(today_wife, "game_service", SimpleNamespace(personal_archive=personal, group_story=group))
    monkeypatch.setattr(today_wife, "feature_scopes", SimpleNamespace(is_feature_group_enabled=lambda *a: True))
    async def avatars(_): return {}
    monkeypatch.setattr(today_wife, "_avatars", avatars)
    path = tmp_path / "wife.png"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(today_wife.renderer, "render_today_wife_archive", lambda *a: path)
    monkeypatch.setattr(today_wife.renderer, "render_group_today_wife", lambda *a: path)
    monkeypatch.setitem(local_features._handlers, action, today_wife._chat_wife_query)
    event = MergedEvent([group_message(group_id=1001, user_id=3)])
    asyncio.run(plugin._run_skill_requests(rig.bot, event, rig.config, (FeatureRequest(action),)))
    assert calls == ([("personal", 1001, 3)] if action == "wife_personal" else [("group", 1001)])
    assert len(rig.bot.sent) == 1 and any(s.type == "image" for s in rig.bot.sent[0][1])


def test_persona_reply_normalization_cannot_turn_clarification_into_execution(rig):
    from bot.services.persona_turn import PersonaTurn
    proposal = {"decision": "clarify", "messages": [], "feature_call": {"action": "ranking", "args": "周"}}
    class Cognition:
        snapshot = SimpleNamespace(prompt=lambda: "synthetic memory contract")
        def __init__(self): self.proposal = proposal
        def current(self): return True
        def apply(self, _): return PersonaTurn._reply_json(self)
    rig.service._cognition_turn.set(Cognition())
    event = group_message(group_id=1001)
    asyncio.run(rig.service._model_reply(rig.bot, event, rig.config, call_text="娅娅看排行"))
    assert not rig.invoked and not rig.bot.sent
