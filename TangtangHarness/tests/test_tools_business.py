import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from functools import wraps
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from tangtang_harness.business.db import Database
from tangtang_harness.store import Store
from tangtang_harness.tools import ToolExecutor
from tangtang_harness.types import InboundEvent, ToolCall, ToolResult
from tangtang_harness.business.mini_games import GameEvent


def test_business_database_context_closes_connection(tmp_path):
    database = Database(tmp_path / "business.db")
    with database.connect() as connection:
        connection.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def run_async(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapper


class Gateway:
    self_id = 999

    def __init__(self):
        self.calls = []

    async def call_api(self, action, **params):
        self.calls.append((action, params))
        if action == "get_group_member_info":
            return {"user_id": params["user_id"], "nickname": "合成成员", "role": "admin"}
        if action == "get_group_member_list":
            return [{"user_id": 101, "nickname": "甲"}, {"user_id": 102, "nickname": "乙"}, {"user_id": 103, "nickname": "丙"}]
        if action == "get_group_list":
            return [{"group_id": 201, "group_name": "合成群"}, {"group_id": 202, "group_name": "合成群二"}]
        if action == "get_group_info":
            return {"group_id": params["group_id"], "group_name": "合成群"}
        if action == "get_login_info":
            return {"user_id": 999, "nickname": "合成机器人"}
        raise AssertionError(f"Unexpected QQ write/API: {action}")


@pytest.fixture
def tools(tmp_path):
    store = Store(tmp_path)
    store.set_setting("operator_ids", [101])
    store.set_setting("avatar_fetch_enabled", False)
    executor = ToolExecutor(Gateway(), tmp_path, store)
    executor.domains.ensure_group(201, group_name="合成群")
    executor.domains.ensure_group(202, group_name="合成群二")
    return executor


def event(text="", user=101, group=201, eid="1", mentions=()):
    return InboundEvent(eid, 999, user, group, text, timestamp=time.time(), sender={"nickname": "甲", "role": "member"},
        segments=tuple({"type": "at", "data": {"qq": str(target)}} for target in mentions))


@run_async
async def test_live_guard_initializes_saved_parameters_and_disabled_guard_does_not_poll(tools, monkeypatch):
    for key, value in {"live_guard_enabled": False, "schedule_refresh_minutes": 12,
                       "live_pause_minutes": 30, "schedule_lookahead_days": 3, "schedule_timeout": 4}.items():
        tools.store.set_setting(key, value)
    recreated = ToolExecutor(tools.gateway, tools.root, tools.store)
    assert recreated.guard.enabled is False
    assert recreated.guard.refresh_minutes == 12
    assert recreated.guard.pause_duration == timedelta(minutes=30)
    assert recreated.guard.lookahead_days == 3
    assert recreated.guard.timeout_seconds == 4
    async def unexpected_refresh():
        raise AssertionError("disabled guard must not poll")
    monkeypatch.setattr(recreated.guard, "refresh_and_apply", unexpected_refresh)
    monkeypatch.setattr(recreated, "_bili_config", lambda: None)
    await recreated.poll_external(group_ids=[])
    await recreated.close()
    await tools.close()


def test_bili_render_cards_setting_agrees_with_status(tools, monkeypatch):
    from tangtang_harness.business import config as business_config
    monkeypatch.setattr(business_config.settings, "asoul_bili_render_cards", True)
    tools.store.set_setting("bili_render_cards", False)
    tools._bili_config()
    assert business_config.settings.asoul_bili_render_cards is False


def image_path(result):
    from urllib.parse import urlparse, unquote
    value = next(part["data"]["file"] for part in result.messages if part["type"] == "image")
    path = unquote(urlparse(value).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return Path(path)


@run_async
async def test_statistics_archive_and_blacklist_keep_local_counts(tools):
    await tools.collect(event("合成正文", user=102))
    await tools.collect(event("合成正文", user=102))
    ranking = await tools.execute(event(), ToolCall("ranking", {"scope": "total"}))
    assert ranking.status == "ok", ranking.text
    assert ranking.data["rows"][0]["message_count"] == 1
    with Image.open(image_path(ranking)) as image:
        assert image.width > 500
    archive = await tools.execute(event(mentions=[102]), ToolCall("archive_records", {"target_user_id": 102}))
    assert archive.status == "ok", archive.text
    assert archive.data["count"] == 1
    tools.db.add_filter_members("active", [102], 101)
    await tools.collect(event("不进入档案", user=102, eid="2"))
    assert (await tools.execute(event(mentions=[102]), ToolCall("archive_records", {"target_user_id": 102}))).data["count"] == 1
    assert (await tools.execute(event(), ToolCall("ranking", {"scope": "total"}))).data["rows"][0]["message_count"] == 2
    assert image_path(ranking).parent.name == "community_html"
    await tools.close()


@run_async
async def test_explicit_numeric_archive_command_retains_old_member_access(tools):
    from tangtang_harness.router import route
    await tools.collect(event("可查询的合成正文", user=103, eid="numeric-archive-source"))
    source = event("#发言记录 103", user=102, eid="numeric-archive-command")
    result = await tools.execute(source, route(source.text, source).tools[0])
    assert result.status == "ok" and result.data["count"] == 1
    assert result.data["target_user_id"] == 103 and image_path(result).is_file()
    unrelated = await tools.execute(event(user=102), ToolCall("archive_records", {"target_user_id": 103}))
    assert unrelated.status == "clarification"
    assert "真实 @" in unrelated.text and not tools.corrections.list_entries()
    await tools.close()


@run_async
async def test_insufficient_archive_profile_keeps_normal_feedback_without_failure_record(tools):
    await tools.collect(event("一条已有正文", eid="insufficient-profile"))
    result = await tools.execute(event(), ToolCall("archive_profile", {}))
    assert result.status == "empty" and "超过 100 条" in result.text
    assert result.data["count"] == 1 and not tools.corrections.list_entries()
    assert len(tools.archive.all_records(101, (201,))) == 1
    await tools.close()


@pytest.mark.parametrize("call,phrase", [
    (ToolCall("archive_records", {"page": 0}), "页码"),
    (ToolCall("guess_submit", {"value": 1000}), "0–999"),
    (ToolCall("duplicate_scan", {"group_ids": [201]}), "至少需要两个群"),
    (ToolCall("duplicate_scan", {"group_ids": [201, 203]}), "受管群"),
    (ToolCall("group_settings", {"action": "set", "feature": "unknown"}), "没有这个群功能"),
    (ToolCall("skill_admin", {"text": "纠错 列表 unknown"}), "open / resolved / all"),
    (ToolCall("skill_admin", {"text": "纠错 查看 missing"}), "没有找到"),
    (ToolCall("skill_admin", {"text": "纠错 记录 ranking missing-category 说明"}), "纠错类别"),
    (ToolCall("skill_admin", {"text": "纠错 unknown"}), "用法"),
    (ToolCall("global_announcement", {"group_ids": [201], "text": "", "raw_image": True}), "附图"),
])
@run_async
async def test_explicit_business_parameter_conditions_remain_visible(tools, call, phrase):
    result = await tools.execute(event(), call)
    assert result.status in {"clarification", "empty"} and phrase in result.text
    assert not tools.corrections.list_entries()
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)
    await tools.close()


@run_async
async def test_robot_status_restores_group_feature_image_without_remote_checks(tools):
    tools.gateway.connected = True
    tools.domains.set_feature(202, "ww", False)
    result = await tools.execute(event(), ToolCall("robot_status", {}))
    assert result.status == "ok" and result.data["groups"] == 2
    assert "已连接" in result.text and image_path(result).is_file()
    assert result.data["group_rows"][0]["detail"].startswith("独群 / ")
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_qq_platform_health_checks_login_current_group_and_member_list(tools):
    result = await tools.execute(event(), ToolCall("qq_platform_health", {}))
    assert result.status == "ok" and result.data["member_count"] == 3 and "成员列表：3 人" in result.text
    assert [action for action, _ in tools.gateway.calls] == ["get_login_info", "get_group_info", "get_group_member_list"]
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)
    await tools.close()


@run_async
async def test_whitelist_restores_folded_text_and_image_outputs(tools):
    await tools.execute(event(), ToolCall("whitelist", {"action": "add", "user_id": 102, "note": "合成备注"}))
    result = await tools.execute(event(), ToolCall("whitelist", {}))
    assert result.status == "ok" and "合成备注" in result.text
    assert len(result.data["forward_nodes"]) == 2 and image_path(result).is_file()
    removed = await tools.execute(event(), ToolCall("whitelist", {"action": "remove", "user_id": 102}))
    assert removed.status == "ok" and "已移除" in removed.text
    await tools.close()


@run_async
async def test_business_entries_provide_clickable_configured_urls(tools):
    tools.store.set_setting("web_base_url", "https://bot.example.invalid/")
    panel = await tools.execute(event(group=None), ToolCall("operator_web", {"page": "duplicate"}))
    url = urlsplit(panel.data["panel"])
    assert url.scheme == "https" and url.netloc == "bot.example.invalid"
    assert url.path == "/business/duplicate"
    token = parse_qs(url.query)["token"][0]
    assert tools.web.state("duplicate", token)["actor_id"] == 101
    help_result = await tools.execute(event(), ToolCall("user_help", {}))
    assert help_result.data["page_url"] == "https://bot.example.invalid/business/help"
    assert help_result.messages[-1]["data"]["text"].startswith("帮助在线：https://")
    await tools.close()


@run_async
async def test_knowledge_review_files_initialize_in_owned_runtime_and_stay_synchronized(tools):
    path = tools.root / "runtime" / "knowledge" / "收录确认.md"
    path.write_text("# 收录确认\n\n### zhijiang/synthetic-file-review\n- domain: zhijiang\n- entry_id: synthetic-file-review\n- title: 合成文件收录\n- summary: 由新系统运行文件导入的条目。\n- tags: 测试\n- source_name: 合成来源\n- source_url: https://example.invalid/test\n", encoding="utf-8")
    recreated = ToolExecutor(tools.gateway, tools.root, tools.store)
    assert recreated.knowledge.find_approved("zhijiang", "synthetic-file-review")
    assert "### zhijiang/synthetic-file-review" not in path.read_text(encoding="utf-8")
    assert recreated.store.get_setting("knowledge_review_files")["approved"] == ["zhijiang/synthetic-file-review"]
    entry = recreated.knowledge.propose_entry(domain="zhijiang", entry_id="synthetic-rejected", title="合成拒绝条目", summary="合成内容。", tags=[], source_name="合成来源", source_url="https://example.invalid/test")
    pending = await recreated.execute(event(), ToolCall("knowledge_review", {}))
    assert f"#{entry}" in pending.text
    await recreated.execute(event(), ToolCall("knowledge_review", {"action": "reject", "entry_id": entry}))
    assert "合成拒绝条目" in (path.parent / "收录拒绝归档.md").read_text(encoding="utf-8")
    assert "合成拒绝条目" not in (path.parent / "收录待审.md").read_text(encoding="utf-8")
    await recreated.execute(event(), ToolCall("knowledge_review", {"action": "revive", "entry_id": entry}))
    assert "合成拒绝条目" in (path.parent / "收录待审.md").read_text(encoding="utf-8")
    await recreated.close()
    await tools.close()


@run_async
async def test_legacy_group_admin_aliases_are_not_mistaken_for_private_system_commands(tools):
    from tangtang_harness.router import route
    for text in ("#主动过滤 103", "#主动过滤列表", "#移除主动过滤 103", "#整点报时 开启", "#总游戏关"):
        source = event(text)
        result = await tools.execute(source, route(text, source).tools[0])
        assert result.status == "ok", (text, result.text)
    assert tools.store.get_setting("hourly_enabled") is True
    assert tools.store.get_setting("mini_games_enabled") is False
    source = event("#系统设置 小游戏 全局 开")
    assert (await tools.execute(source, route(source.text, source).tools[0])).status == "denied"
    await tools.close()


@run_async
async def test_legacy_passive_parameter_names_and_units_still_control_real_settings(tools):
    from tangtang_harness.router import route
    for parameter, value, key, expected in (("表情命中率", "50", "reaction_probability", .5),
                                            ("复读命中率", "10%", "repeat_probability", .1),
                                            ("复读冷却", "15", "repeat_cooldown", 900),
                                            ("三连复读", "开", "triple_enabled", True),
                                            ("三连命中率", "1%", "triple_probability", .01)):
        source = event(f"#系统设置 被动互动 201 {parameter} {value}", group=None)
        result = await tools.execute(source, route(source.text, source).tools[0])
        assert result.status == "ok" and result.data["config"][key] == expected
    await tools.close()


@run_async
async def test_members_view_only_enabled_group_features_and_admins_keep_filter_list_access(tools):
    from tangtang_harness.router import route
    tools.domains.set_feature(201, "ww", False)
    tools.db.add_group_filter(201, 103, 101)
    normal = await tools.execute(event("#群设置", user=102), ToolCall("group_feature_status"))
    assert all(row["configured_enabled"] for row in normal.data["features"])
    assert "鸣潮：关" not in normal.text and "filters" not in normal.data
    text = "#群设置 过滤 列表"
    member = event(text, user=102)
    assert (await tools.execute(member, route(text, member).tools[0])).status == "denied"
    admin = event(text)
    result = await tools.execute(admin, route(text, admin).tools[0])
    assert result.status == "ok" and "103" in result.text
    await tools.close()


@run_async
async def test_zhijiang_schedule_reuses_current_week_calendar_not_only_active_streams(tools, monkeypatch):
    asked = []
    async def days(first, last):
        asked.append((first, last))
        return {}
    async def render(payload, prefix):
        return tools.report_dir / "synthetic-schedule.png"
    monkeypatch.setattr(tools.asoul, "schedule_for_days", days)
    monkeypatch.setattr(tools.asoul_web, "render_payload", render)
    result = await tools.execute(event("#枝江直播"), ToolCall("zhijiang_schedule"))
    assert result.status == "ok" and result.data["view"] == "week"
    first, last = asked[0]
    assert last.weekday() == 6 and first == tools._now().date()
    assert result.data["page_url"].endswith("/business/schedule?view=week")
    await tools.close()


@run_async
async def test_live_guard_status_uses_local_card_and_refresh_failure_is_recorded(tools, monkeypatch):
    async def unavailable():
        tools.db.set_passive_setting("zhijiang_live_schedule_last_error", "ConnectError: synthetic network failure")
        return ()
    monkeypatch.setattr(tools.guard, "refresh_and_apply", unavailable)
    result = await tools.execute(event("#枝江直播 状态"), ToolCall("zhijiang_status"))
    assert result.status == "ok" and image_path(result).is_file()
    failed = await tools.execute(event("#刷新枝江直播"), ToolCall("zhijiang_refresh"))
    assert failed.status == "error" and not failed.messages
    assert failed.data["status"]["last_error"].startswith("ConnectError")
    assert tools.corrections.list_entries()[0]["skill_id"] == "zhijiang_refresh"
    await tools.close()


@run_async
async def test_realtime_stats_off_preserves_archive_activity_passive_and_event_dedup(tools):
    tools.store.set_setting("stats_realtime_enabled", False)
    tools.domains.set_feature(201, "passive_interaction", True)
    tools.store.set_setting("passive:201", {"repeat_enabled": True, "repeat_probability": 1,
                                          "repeat_interval": 1, "repeat_cooldown": 0})
    incoming = event("仍可归档和复读", user=102, eid="stats-paused")
    result = await tools.collect(incoming, passive=True)
    assert result.text == incoming.text
    assert await tools.collect(incoming, passive=True) is None
    await tools.collect_outgoing(event(), "已确认发送", "stats-paused-out")
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_counts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM event_dedup").fetchone()[0] == 2
        assert conn.execute("SELECT message_count FROM today_wife_activity_counts WHERE group_id=201 AND user_id=102").fetchone()[0] == 1
    assert len(tools.archive.all_records(102, (201,))) == 1
    assert len(tools.archive.all_records(999, (201,))) == 1
    tools.store.set_setting("stats_realtime_enabled", True)
    assert await tools.collect(incoming, passive=True) is None
    await tools.collect_outgoing(event(), "已确认发送", "stats-paused-out")
    await tools.collect(event("恢复统计", user=102, eid="stats-resumed"))
    await tools.collect_outgoing(event(), "恢复发送统计", "stats-resumed-out")
    await tools.collect(event("已确认发送回声", user=999, eid="stats-resumed-out"))
    with tools.db.connect() as conn:
        counts = {row["user_id"]: row["message_count"] for row in conn.execute("SELECT user_id,message_count FROM daily_counts WHERE group_id=201")}
        assert counts == {102: 1, 999: 1}
        assert conn.execute("SELECT message_count FROM today_wife_activity_counts WHERE group_id=201 AND user_id=102").fetchone()[0] == 2
    assert len(tools.archive.all_records(102, (201,))) == 2
    assert len(tools.archive.all_records(999, (201,))) == 2
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)
    await tools.close()


@run_async
async def test_passive_repeat_requires_plain_text_and_ignores_game_or_bot_mentions(tools):
    tools.domains.set_feature(201, "passive_interaction", True)
    tools.store.set_setting("passive:201", {"repeat_enabled": False, "triple_enabled": True, "triple_probability": 1})
    for index in range(3):
        source = replace(event("图片说明", user=102, eid=f"mixed-caption-{index}"), segments=(
            {"type": "text", "data": {"text": "图片说明"}}, {"type": "image", "data": {"file": "synthetic.png"}}))
        assert await tools.collect(source, passive=True) is None
    assert await tools.collect(event("图片说明", user=102, eid="one-plain"), passive=True) is None
    tools.store.set_setting("passive:201", {"repeat_enabled": True, "repeat_probability": 1, "repeat_interval": 1, "repeat_cooldown": 0})
    assert await tools.collect(event("ww帮助", user=102, eid="bare-game"), passive=True) is None
    assert await tools.collect(event("向机器人说话", user=102, eid="mention-bot", mentions=[999]), passive=True) is None
    repeated = await tools.collect(event("普通纯文本", user=102, eid="plain-repeat"), passive=True)
    assert repeated.text == "普通纯文本"
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_ranking_query_uses_current_web_surface_for_every_scope(tools, monkeypatch):
    payloads = []
    async def render(payload):
        payloads.append(payload)
        return tools.report_dir / "ranking.png"
    monkeypatch.setattr(tools.community_renderer, "render_ranking", render)
    await tools.collect(event("合成发言", eid="style-1"))
    await tools.collect(event("合成发言二", user=102, group=202, eid="style-2"))
    for scope in ("day", "week", "month", "total"):
        result = await tools.execute(event(), ToolCall("ranking", {"scope": scope}))
        assert result.status == "ok"
        assert result.data["page_url"].endswith(f"group_id=201&group=current&scope={scope}")
        payload = payloads[-1]
        assert payload["scope"] == scope
        assert payload["group_label"] == "合成群"
        assert payload["rows"][0]["rank"] == 1
        assert payload["chart"]["kind"] == "daily"
        web_payload = await tools.web.read("ranking", group_id=201, scope=scope)
        for key in ("title", "rows", "chart", "message_total"):
            assert payload[key] == web_payload[key]
    domain = tools.domains.create_cluster("合成集群")
    tools.domains.add_group_to_cluster(201, domain.domain_id)
    tools.domains.add_group_to_cluster(202, domain.domain_id)
    result = await tools.execute(event(), ToolCall("ranking", {"scope": "total", "cluster": True}))
    assert result.status == "ok"
    assert result.data["page_url"].endswith("group_id=201&group=domain&scope=total")
    assert payloads[-1]["group_label"] == "合成集群"
    assert payloads[-1]["message_total"] == 2
    assert payloads[-1]["chart"]["kind"] == "group"
    assert {row["label"] for row in payloads[-1]["chart"]["rows"]} == {"合成群", "合成群二"}
    await tools.close()


@run_async
async def test_daily_ranking_push_uses_web_style_and_preserves_previous_day(tools, monkeypatch):
    payloads = []
    async def render(payload):
        payloads.append(payload)
        return tools.report_dir / "ranking.png"
    monkeypatch.setattr(tools.community_renderer, "render_ranking", render)
    now = datetime.fromisoformat("2026-10-05T00:05:00+08:00")
    monkeypatch.setattr(tools, "_now", lambda timestamp=0: now)
    tools.domains.set_feature(201, "speech_ranking_push", True)
    tools.db.record_message("201:day-1", 201, 101, "甲", now - timedelta(minutes=20))
    tools.db.record_message("201:day-2", 201, 102, "乙", now)
    pending = await tools.tick(now, group_ids=[201])
    assert len(pending) == 1
    payload = payloads[0]
    assert payload["scope_title"] == "10.04发言榜"
    assert payload["generated_date"] == "2026.10.04"
    assert payload["message_total"] == 1
    assert [row["nickname"] for row in payload["rows"]] == ["甲"]
    assert payload["chart"]["rows"][-1] == {"label": "10.04", "message_count": 1, "avatar": ""}
    tools.mark_delivered(pending[0]["result"], True)
    assert not await tools.tick(now + timedelta(seconds=35), group_ids=[201])
    await tools.close()


@run_async
async def test_daily_ranking_push_aggregates_cluster_for_each_member(tools, monkeypatch):
    payloads = []

    async def render(payload):
        payloads.append(payload)
        return tools.report_dir / "cluster-ranking.png"

    monkeypatch.setattr(tools.community_renderer, "render_ranking", render)
    domain = tools.domains.create_cluster("合成集群")
    tools.domains.add_group_to_cluster(201, domain.domain_id)
    tools.domains.add_group_to_cluster(202, domain.domain_id)
    tools.domains.set_feature(201, "speech_ranking_push", True)
    tools.domains.set_feature(202, "speech_ranking_push", True)
    now = datetime.fromisoformat("2026-10-05T00:05:00+08:00")
    previous = now - timedelta(minutes=20)
    for index in range(2):
        tools.db.record_message(f"cluster-a-{index}", 201, 101, "甲", previous)
    for index in range(3):
        tools.db.record_message(f"cluster-b-{index}", 202, 102, "乙", previous)

    pending = await tools.tick(now, group_ids=[201, 202])
    assert [item["group_id"] for item in pending] == [201, 202]
    assert len(payloads) == 1
    assert payloads[0]["group_label"] == "合成集群"
    assert payloads[0]["message_total"] == 5
    assert [row["nickname"] for row in payloads[0]["rows"]] == ["乙", "甲"]
    assert pending[0]["result"].messages == pending[1]["result"].messages
    await tools.close()


@run_async
async def test_real_five_games_render_and_group_isolation(tools):
    for name in ("roulette_load", "bomb_load", "idiom_bomb_load", "dice_start", "guess_start"):
        tools.games.cancel_group_session(201)
        result = await tools.execute(event(), ToolCall(name, {}))
        assert result.status == "ok", (name, result.text)
        assert result.data["game_type"]
    tools.games.cancel_group_session(201)
    result = await tools.execute(event(), ToolCall("mini_game_help", {}))
    assert result.status == "ok", result.text
    assert result.data["random_event_count"] >= 40
    assert result.data["additional_messages"]
    tools.domains.set_feature(201, "mini_games", False)
    assert (await tools.execute(event(), ToolCall("dice_start", {}))).status == "denied"
    assert (await tools.execute(event(group=202), ToolCall("dice_start", {}))).status == "ok"


@run_async
async def test_wife_draw_history_and_group_chart_use_mature_business(tools):
    for user in (101, 102, 103):
        await tools.collect(event("活跃", user=user, eid=str(user)))
    result = await tools.execute(event(), ToolCall("wife_draw", {}))
    assert result.status == "ok", result.text
    target = result.data["record"]["target_id"]
    assert target in (102, 103)
    assert (await tools.execute(event(), ToolCall("wife_draw", {}))).data["record"]["target_id"] == target
    for name, args in (("wife_personal", {}), ("wife_personal", {"page": 2}), ("wife_group", {}), ("wife_group", {"history": True})):
        result = await tools.execute(event(), ToolCall(name, args))
        assert result.status == "ok", (name, result.text)
        assert image_path(result).is_file()
    assert (await tools.execute(event(), ToolCall("wife_divorce", {}))).status == "ok"
    assert (await tools.execute(event(), ToolCall("wife_draw", {}))).status == "ok"


@run_async
async def test_wife_reveal_mentions_only_first_draw_and_renders_frozen_divorce(tools, monkeypatch):
    monkeypatch.setattr(tools.wife_game, "is_locked", lambda group: False)
    for user in (101, 102, 103):
        await tools.collect(event("活跃", user=user, eid=f"wife-reveal-{user}"))
    drawn = await tools.execute(event(mentions=[102]), ToolCall("wife_take", {"target_user_id": 102}))
    assert drawn.status == "ok" and drawn.data["record"]["target_id"] == 102
    assert drawn.data["draw_reveal"]["blocks"] and drawn.data["day_state"]["script_title"]
    assert drawn.data["relation"] and image_path(drawn).is_file()
    assert image_path(drawn).name.startswith("today_wife_draw")
    assert drawn.messages[0] == {"type": "reply", "data": {"id": "1"}}
    assert [part["data"]["qq"] for part in drawn.messages if part["type"] == "at"] == ["102"]
    repeated = await tools.execute(event(), ToolCall("wife_draw", {}))
    assert image_path(repeated).name.startswith("today_wife_draw")
    assert not any(part["type"] == "at" for part in repeated.messages)
    refused = await tools.execute(event(mentions=[103]), ToolCall("wife_take", {"target_user_id": 103}))
    assert refused.status == "empty" and refused.data["kind"] == "force_existing"
    assert "已经有老婆（乙）" in refused.text and "#离婚" in refused.text
    assert refused.data["record"]["target_id"] == 102 and not refused.messages
    divorced = await tools.execute(event(), ToolCall("wife_divorce", {}))
    assert divorced.status == "ok" and image_path(divorced).is_file()
    assert image_path(divorced).name.startswith("today_wife_divorce")
    assert divorced.data["relation"]["mood"] == "separated"
    redraw = await tools.execute(event(mentions=[103]), ToolCall("wife_take", {"target_user_id": 103}))
    assert redraw.status == "ok" and redraw.data["record"]["draw_index"] == 2
    assert not any(part["type"] == "at" for part in redraw.messages)
    assert (await tools.execute(event(), ToolCall("wife_divorce", {}))).status == "ok"
    exhausted = await tools.execute(event(mentions=[102]), ToolCall("wife_take", {"target_user_id": 102}))
    assert exhausted.status == "empty" and exhausted.data["kind"] == "existing_divorced"
    assert "重抽机会已经用过" in exhausted.text and not exhausted.messages
    archived = await tools.execute(event(), ToolCall("wife_draw", {}))
    assert archived.status == "ok" and "关系已留档" in archived.text
    assert image_path(archived).name.startswith("today_wife_divorce")
    with tools.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM today_wife_records WHERE group_id=201 AND actor_id=101").fetchone()[0] == 2
    assert not tools.corrections.list_entries()
    await tools.close()


@run_async
async def test_wife_divorce_respects_concluded_story_without_changing_relationship(tools, monkeypatch):
    await tools.collect(event("活跃", user=102, eid="wife-lock-candidate"))
    outcome = tools.wife.draw(201, 101, "甲", [{"user_id": 101, "nickname": "甲"}, {"user_id": 102, "nickname": "乙"}])
    assert outcome.record["status"] == "active"
    monkeypatch.setattr(tools.wife_game, "is_locked", lambda group: True)
    result = await tools.execute(event(), ToolCall("wife_divorce", {}))
    assert result.status == "empty" and "收官" in result.text
    assert not tools.corrections.list_entries()
    with tools.db.connect() as conn:
        assert conn.execute("SELECT status FROM today_wife_records WHERE group_id=201 AND actor_id=101").fetchone()[0] == "active"
    await tools.close()


@run_async
async def test_wife_normal_empty_and_parameter_states_keep_mature_text(tools, monkeypatch):
    monkeypatch.setattr(tools.wife_game, "is_locked", lambda group: False)
    missing = await tools.execute(event(), ToolCall("wife_divorce", {}))
    assert missing.status == "empty" and missing.data["kind"] == "not_found"
    assert missing.text == tools.wife.state_message("not_found", 201, 101)
    async def only_actor(group):
        return [{"user_id": 101, "nickname": "甲"}]
    monkeypatch.setattr(tools.platform, "member_list", only_actor)
    empty = await tools.execute(event(), ToolCall("wife_draw", {}))
    assert empty.status == "empty" and empty.data["kind"] == "no_candidates"
    assert empty.text == tools.wife.state_message("no_candidates", 201, 101)
    invalid = await tools.execute(event(mentions=[404]), ToolCall("wife_take", {"target_user_id": 404}))
    assert invalid.status == "clarification" and invalid.data["kind"] == "force_invalid_target"
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM today_wife_records").fetchone()[0] == 0
    await tools.collect(event("活跃", user=102, eid="wife-normal-candidate"))
    drawn = tools.wife.draw(201, 101, "甲", [{"user_id": 101, "nickname": "甲"}, {"user_id": 102, "nickname": "乙"}])
    assert drawn.record is not None
    assert (await tools.execute(event(), ToolCall("wife_divorce", {}))).status == "ok"
    repeated = await tools.execute(event(), ToolCall("wife_divorce", {}))
    assert repeated.status == "empty" and repeated.data["kind"] == "already_divorced"
    assert repeated.text == tools.wife.state_message("already_divorced", 201, 101)
    assert not tools.corrections.list_entries()
    await tools.close()


@pytest.mark.parametrize("name,feature", [("mini_game_clear", "mini_games"), ("wife_clear", "today_wife")])
@run_async
async def test_admin_cleanup_keeps_confirmation_when_business_is_disabled(tools, name, feature):
    tools.domains.set_feature(201, feature, False)
    assert (await tools.execute(event(user=102), ToolCall(name, {}))).status == "denied"
    assert (await tools.execute(event(), ToolCall(name, {"confirm": True}))).status == "clarification"
    assert (await tools.execute(event(), ToolCall(name, {}))).status == "clarification"
    assert (await tools.execute(event(), ToolCall(name, {"confirm": True}))).status == "ok"
    await tools.close()


@run_async
async def test_knowledge_whitelist_filters_and_settings_are_real(tools):
    result = await tools.execute(event(), ToolCall("knowledge_search", {"query": "嘉然"}))
    assert result.status == "ok" and result.data["entries"]
    entry = tools.knowledge.propose_entry(domain="zhijiang", entry_id="synthetic-test", title="合成新词", summary="合成解释", source_name="合成来源", source_url="https://example.invalid")
    assert tools.knowledge.pending_entries()
    assert (await tools.execute(event(), ToolCall("knowledge_review", {"action": "approve", "entry_id": entry}))).status == "ok"
    assert tools.knowledge.find_approved("zhijiang", "synthetic-test")
    assert (await tools.execute(event(), ToolCall("whitelist", {"action": "add", "user_id": 102}))).status == "ok"
    assert tools.db.whitelist_contains(102)
    result = await tools.execute(event(group=None), ToolCall("system_settings", {"key": "hourly_schedule", "action": "set", "value": ["08:00", "22:00"]}))
    assert result.status == "ok" and tools.store.get_setting("hourly_end") == "22:00"
    result = await tools.execute(event(group=None), ToolCall("system_settings", {"key": "passive:201", "action": "configure", "parameter": "复读概率", "value": "10%"}))
    assert result.status == "ok" and result.data["config"]["repeat_probability"] == .1
    assert (await tools.execute(event(user=102), ToolCall("whitelist", {}))).status == "denied"


@run_async
async def test_observe_game_results_only_declare_qq_writes(tools):
    tools.store.set_setting("game_mute:201", True)
    result = await tools._game_result(GameEvent(201, "guess", "未猜中", "guess_timeout", mute_user_ids=(102, 103), half_mute_user_ids=(103,)))
    assert all(name != "set_group_ban" for name, _ in tools.gateway.calls)
    actions = result.data["actions"]
    assert 30 <= actions[0]["params"]["duration"] <= 60
    assert actions[1]["params"]["duration"] == actions[0]["params"]["duration"] // 2


@pytest.mark.parametrize("disabled,native_disabled,selected,group_enabled,expected", [
    ("", None, None, True, True),
    ("201, 202", None, None, True, False),
    ("201", [], None, True, True),
    ("", [201], None, True, False),
    ("201", [201], True, True, True),
    ("", [], False, True, False),
    ("", [], None, False, False),
])
@run_async
async def test_game_mute_keeps_legacy_default_and_explicit_harness_selection(
        tools, disabled, native_disabled, selected, group_enabled, expected):
    tools.db.set_passive_setting("game_mute_disabled_group_ids", disabled)
    if native_disabled is not None:
        tools.store.set_setting("game_mute_disabled_group_ids", native_disabled)
    if selected is not None:
        tools.store.set_setting("game_mute:201", selected)
    tools.domains.set_feature(201, "mini_games", group_enabled)
    result = await tools._game_result(GameEvent(201, "guess", "未猜中", "guess_timeout", mute_user_ids=(102,)))
    assert bool(result.data.get("actions")) is expected
    status = await tools.execute(event(), ToolCall("system_settings", {"key": "game_mute:201", "action": "status"}))
    assert status.data["value"] is expected
    assert not any(name == "set_group_ban" for name, _ in tools.gateway.calls)
    await tools.close()


@run_async
async def test_tick_scope_does_not_expire_unselected_games(tools):
    now = datetime.fromisoformat("2026-10-04T10:00:00+08:00")
    tools.games.start_guess(201, 101, "甲", now)
    tools.games.start_guess(202, 102, "乙", now)
    with tools.db.connect() as conn:
        conn.execute("UPDATE mini_game_sessions SET ends_at=? WHERE status='active'", (tools.games._timestamp(now - timedelta(seconds=1)),))
    pending = await tools.tick(now, group_ids=[201])
    assert [item["group_id"] for item in pending] == [201]
    with tools.db.connect() as conn:
        assert conn.execute("SELECT status FROM mini_game_sessions WHERE group_id=202").fetchone()[0] == "active"


@run_async
async def test_hourly_retry_is_persisted_and_import_cutoff_blocks_backfill(tools):
    now = datetime.fromisoformat("2026-10-04T10:00:00+08:00")
    tools.domains.set_feature(201, "hourly", True)
    tools.domains.set_feature(202, "hourly", True)
    tools.store.set_setting("hourly_enabled", True)
    tools.store.set_setting("import_cutoff", now.timestamp() + 1)
    assert await tools.tick(now, group_ids=[201]) == []
    tools.store.set_setting("import_cutoff", 0)
    pending = await tools.tick(now, group_ids=[201])
    assert len(pending) == 1 and pending[0]["group_id"] == 201
    assert not await tools.tick(now + timedelta(seconds=1), group_ids=[201])
    tools.mark_delivered(pending[0]["result"], True)
    assert not await tools.tick(now + timedelta(seconds=35), group_ids=[201])


@run_async
async def test_bili_queue_survives_restart_until_all_scoped_receipts(tools):
    now = datetime.now().astimezone()
    tools.domains.set_feature(201, "bilibili", True)
    tools.domains.set_feature(202, "bilibili", True)
    with tools.db.connect() as conn:
        conn.execute("INSERT INTO harness_bili_updates VALUES(?,?,?,?,?)", ("synthetic", "合成B站通知", "text", "null", "[201,202]"))
    pending = await tools.tick(now, group_ids=[201])
    assert len(pending) == 1 and pending[0]["group_id"] == 201
    tools.mark_delivered(pending[0]["result"], True)
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM harness_bili_updates").fetchone()[0] == 1
    restarted = ToolExecutor(tools.gateway, tools.root, tools.store)
    pending = await restarted.tick(now, group_ids=[202])
    assert len(pending) == 1 and pending[0]["group_id"] == 202
    restarted.mark_delivered(pending[0]["result"], True)
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM harness_bili_updates").fetchone()[0] == 0


def configure_bili_polling(tools, monkeypatch, *, push_video=False, push_live=False):
    from tangtang_harness.business import config as business_config
    for key, value in {"enabled": True, "target_uids": ("100",), "comment_target_uids": (),
                       "push_dynamic": False, "push_video": push_video, "push_live": push_live,
                       "push_comment": False, "render_cards": False, "poll_interval_seconds": 60}.items():
        monkeypatch.setattr(business_config.settings, "asoul_bili_" + key, value)
        tools.store.set_setting("bili_" + key, value)


@pytest.mark.parametrize("group_ids,enabled", [([], True), ([202], True), ([201], False)])
@run_async
async def test_bili_empty_receiving_scope_preserves_video_cursor_until_recovery(tools, monkeypatch, group_ids, enabled):
    configure_bili_polling(tools, monkeypatch, push_video=True)
    tools.domains.set_feature(201, "bilibili", enabled)
    baseline = {"initialized": True, "100": {"dynamic_initialized": True, "dynamic_ids": ["old"]}}
    tools.db.set_asoul_state("bilibili_monitor", baseline)
    scanned, guard_refreshes = [], []

    async def dynamics(uid):
        scanned.append(uid)
        return [{"id": "new", "uid": uid, "author": "合成主播", "text": "合成视频",
                 "url": "https://example.invalid/video", "notification_kind": "video"}]

    async def live(uids):
        return {}

    async def enrich(item):
        return item

    async def refresh():
        guard_refreshes.append(True)

    monkeypatch.setattr(tools.asoul, "fetch_dynamics", dynamics)
    monkeypatch.setattr(tools.asoul, "fetch_live_statuses", live)
    monkeypatch.setattr(tools.asoul, "enrich_video_notification", enrich)
    monkeypatch.setattr(tools.guard, "refresh_and_apply", refresh)

    await tools.poll_external(group_ids=group_ids)
    assert scanned == [] and guard_refreshes == [True]
    assert "bili" not in tools._last_external
    assert tools.db.asoul_state("bilibili_monitor") == baseline
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM harness_bili_updates").fetchone()[0] == 0

    tools.domains.set_feature(201, "bilibili", True)
    await tools.poll_external(group_ids=[201])
    assert scanned == ["100"]
    assert "new" in tools.db.asoul_state("bilibili_monitor")["100"]["dynamic_ids"]
    with tools.db.connect() as conn:
        row = conn.execute("SELECT kind,targets_json FROM harness_bili_updates").fetchone()
        assert row["kind"] == "video" and json.loads(row["targets_json"]) == [201]
    pending = await tools.tick(datetime.fromisoformat("2026-10-05T13:15:00+08:00"), group_ids=[201])
    assert len(pending) == 1 and pending[0]["group_id"] == 201
    tools.mark_delivered(pending[0]["result"], True)
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_bili_same_text_live_sessions_each_deliver_and_failed_send_keeps_its_key(tools, monkeypatch):
    configure_bili_polling(tools, monkeypatch, push_live=True)
    tools.domains.set_feature(201, "bilibili", True)
    tools.guard.enabled = False
    tools.db.set_asoul_state("bilibili_monitor", {"initialized": True, "100": {"live": "0"}})
    status = {"live": "0"}
    clock = [1000.0]
    now = datetime.fromisoformat("2026-10-05T13:15:00+08:00")

    async def live(uids):
        return {"100": {"id": "1", "uid": "100", "author": "合成主播", "text": "固定直播标题",
                        "url": "https://example.invalid/live", "live": status["live"]}}

    async def enrich(item):
        return item

    monkeypatch.setattr(tools.asoul, "fetch_live_statuses", live)
    monkeypatch.setattr(tools.asoul, "enrich_live_notification", enrich)
    monkeypatch.setattr("tangtang_harness.tools.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("tangtang_harness.tools.time.time", lambda: now.timestamp())
    delivered = []
    for index, live_status in enumerate(("1", "0", "1", "0")):
        status["live"] = live_status
        clock[0] += 60
        await tools.poll_external(group_ids=[201])
        outputs = await tools.tick(now, group_ids=[201])
        assert len(outputs) == 1
        result = outputs[0]["result"]
        if index == 0:
            tools.mark_delivered(result, False)
            assert not await tools.tick(now + timedelta(seconds=1), group_ids=[201])
            retried = await tools.tick(now + timedelta(seconds=70), group_ids=[201])
            assert len(retried) == 1
            assert retried[0]["result"].data["delivery"]["key"] == result.data["delivery"]["key"]
            assert retried[0]["result"].messages == result.messages
            result = retried[0]["result"]
        tools.mark_delivered(result, True)
        delivered.append(result)
        clock[0] += 60
        await tools.poll_external(group_ids=[201])
        assert not await tools.tick(now, group_ids=[201])

    assert delivered[0].text == delivered[2].text
    assert delivered[1].text == delivered[3].text
    assert len({result.data["delivery"]["key"] for result in delivered}) == 4
    with tools.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM harness_bili_updates").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM harness_pending_outputs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM harness_deliveries WHERE delivered=1").fetchone()[0] == 4
    assert all(action == "get_group_member_info" for action, _ in tools.gateway.calls)
    await tools.close()


@pytest.mark.parametrize("kind,cards", [("dynamic", True), ("video", True), ("dynamic", False), ("video", False)])
@run_async
async def test_bili_dynamic_and_video_notifications_keep_clickable_url(tools, monkeypatch, kind, cards):
    path = tools.root / "runtime" / "synthetic-bili-card.png"
    Image.new("RGB", (24, 24), "white").save(path)
    async def render(text, **kwargs):
        assert kwargs[kind]["url"] == "https://example.invalid/item"
        return path
    monkeypatch.setattr(tools.asoul_web, "render_notification", render)
    tools.store.set_setting("bili_render_cards", cards)
    segments = await tools._notification_segments("合成通知", kind, {"url": "https://example.invalid/item"})
    assert segments[-1]["type"] == "text"
    assert segments[-1]["data"]["text"].strip() == "https://example.invalid/item"
    assert any(part["type"] == "image" for part in segments) is cards
    assert tools.gateway.calls == []
    await tools.close()


@pytest.mark.parametrize("role,allowed", [("admin", True), ("owner", True), ("member", False), ("unavailable", False)])
@run_async
async def test_bili_live_push_checks_each_group_role_and_retries_same_message(tools, monkeypatch, role, allowed):
    tools.store.set_setting("bili_render_cards", False)
    for group in (201, 202):
        tools.domains.set_feature(group, "bilibili", True)
    calls = []
    async def member(group, user):
        calls.append((group, user))
        if group == 201 and role == "unavailable":
            raise TimeoutError("synthetic-role-detail")
        return {"role": role if group == 201 else "member"}
    monkeypatch.setattr(tools.platform, "member_info", member)
    with tools.db.connect() as conn:
        conn.execute("INSERT INTO harness_bili_updates VALUES(?,?,?,?,?)",
            ("synthetic-live", "【开播】合成直播", "live", "null", "[201,202]"))
    now = datetime(2026, 10, 5, 13, 15).astimezone()
    monkeypatch.setattr("tangtang_harness.tools.time.time", lambda: now.timestamp())
    outputs = await tools.tick(now, group_ids=[201, 202])
    assert calls == [(201, 999), (202, 999)]
    by_group = {item["group_id"]: item["result"] for item in outputs}
    assert len(by_group) == 2
    assert any(part["type"] == "at" and part["data"]["qq"] == "all" for part in by_group[201].messages) is allowed
    assert not any(part["type"] == "at" for part in by_group[202].messages)
    assert "synthetic-role-detail" not in by_group[201].text
    if role == "unavailable":
        assert by_group[201].data["atall_validation_error"] == "TimeoutError"
    for item in outputs:
        tools.mark_delivered(item["result"], False)
    retried = await tools.tick(now + timedelta(seconds=70), group_ids=[201, 202])
    assert calls == [(201, 999), (202, 999)]
    assert {item["group_id"]: item["result"].messages for item in retried} == {group: result.messages for group, result in by_group.items()}
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_duplicate_whitelist_and_feature_switches_use_local_services(tools):
    result = await tools.execute(event(), ToolCall("duplicate_scan", {"group_ids": [201, 202], "mode": "all"}))
    assert result.status == "ok", result.text
    assert len(result.data["members"]) == 3
    await tools.execute(event(), ToolCall("whitelist", {"action": "add", "user_id": 102}))
    assert len((await tools.execute(event(), ToolCall("duplicate_scan", {"group_ids": [201, 202], "mode": "source"}))).data["members"]) == 2
    await tools.execute(event(), ToolCall("skill_admin", {"text": "开关 关 dice_start"}))
    assert (await tools.execute(event(), ToolCall("dice_start", {}))).status == "disabled"
    await tools.execute(event(), ToolCall("skill_admin", {"text": "开关 开 dice_start"}))
    assert (await tools.execute(event(), ToolCall("dice_start", {}))).status == "ok"
    assert image_path(await tools.execute(event(), ToolCall("denia_gallery", {}))).is_file()


@run_async
async def test_default_duplicate_and_pending_outputs_exclude_archived_groups(tools, monkeypatch):
    tools.domains.ensure_group(203, group_name="已离开的合成群")
    tools.domains.disable_group(203)
    groups_seen = []

    async def scan(platform, groups, ignore_whitelist):
        groups_seen.append(groups)
        return [], []

    monkeypatch.setattr(tools.duplicate, "scan_all", scan)
    monkeypatch.setattr(tools.renderer, "render_duplicate", lambda *args: tools.report_dir / "duplicate.png")
    result = await tools.execute(event(group=None), ToolCall("duplicate_scan", {"mode": "all"}))
    assert result.status == "ok" and groups_seen == [(201, 202)]
    denied = await tools.execute(event(group=None), ToolCall("duplicate_scan", {"group_ids": [201, 203]}))
    assert denied.status == "clarification" and groups_seen == [(201, 202)]
    now = datetime.fromisoformat("2026-10-05T12:15:00+08:00")
    tools._queue_output(203, "custom:203:notice", ToolResult("ok", "离开后的通知"), now, immediate=True)
    assert await tools.tick(now, group_ids=[203]) == []
    assert (await tools.execute(event(group=203), ToolCall("user_help"))).status == "disabled"
    await tools.close()


def test_database_duplicate_ignores_archived_group_members(tools):
    tools.domains.ensure_group(203, group_name="已归档群")
    for group in (201, 202, 203):
        tools.db.replace_members(group, [{"user_id": 101, "nickname": "合成成员"}])
    tools.domains.disable_group(203)
    assert tools.db.duplicate_members((201, 203)) == []
    assert tools.db.duplicate_members_from_source(201, (203,)) == []
    assert tools.db.duplicate_members_from_source(203, (201, 202)) == []
    rows = tools.db.duplicate_members((201, 202, 203))
    assert [group["group_id"] for group in rows[0]["groups"]] == [201, 202]


@run_async
async def test_business_pages_have_real_payloads_and_private_web_controls(tools):
    tools.domains.set_alias(201, "短名")
    for name in ("help", "ranking", "schedule", "duplicate", "whitelist", "announcement"):
        token = ""
        if name in {"duplicate", "whitelist", "announcement"}:
            result = await tools.execute(event(group=None), ToolCall("operator_web", {"page": name}))
            token = parse_qs(urlsplit(result.data["panel"]).query)["token"][0]
        source = await tools.page(name, group_id=201, token=token)
        assert "<!DOCTYPE html>" in source or "<!doctype html>" in source
        assert "请在控制台工具页调用" not in source
    duplicate_token = next(token for token, session in tools.web.sessions.items() if session["name"] == "duplicate")
    assert tools.web.state("duplicate", duplicate_token)["groups"][0]["name"] == "合成群"
    assert (await tools.web.read("ranking", group_id=201))["group_label"] == "短名"
    token = next(token for token, session in tools.web.sessions.items() if session["name"] == "announcement")
    state = tools.web.state("announcement", token)
    assert len(state["target_options"]) == 2
    assert next(item for item in state["target_options"] if item["key"] == "group:201")["label"] == "合成群"
    assert set(state["members"]) >= {"嘉然", "心宜"}
    result = await tools.web.action("announcement", token, "preview", {"text": "合成公告", "targets": ["group:201"], "member": "__none__"})
    assert result.status == "ok" and Path(result.data["image_path"]).is_file()
    selected = await tools.web.action("announcement", token, "send-preview", {})
    assert selected.data["deliveries"][0]["group_id"] == 201
    with pytest.raises(ValueError, match="预览"):
        await tools.web.action("announcement", token, "send-preview", {})
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)


@pytest.mark.parametrize("hour,period", [(0, "night"), (1, None), (2, "night"), (3, None), (4, "night"),
    (5, None), (6, "morning"), (9, "morning"), (10, "daytime"), (17, "evening"), (22, "night"), (23, "night")])
@run_async
async def test_hourly_schedule_keeps_mature_night_and_period_rules(tools, monkeypatch, hour, period):
    from types import SimpleNamespace
    tools.domains.set_feature(201, "hourly", True)
    tools.domains.set_feature(201, "today_wife", False)
    tools.domains.set_feature(201, "speech_ranking_push", False)
    tools.store.set_setting("hourly_enabled", True)
    tools.store.set_setting("hourly_start", "00:00")
    tools.store.set_setting("hourly_end", "23:00")
    calls = []
    def compose(selected, blocked):
        calls.append((selected, blocked))
        return SimpleNamespace(text="合成报时", fingerprint=1)
    monkeypatch.setattr(tools.hourly, "compose", compose)
    outputs = await tools.tick(datetime(2026, 10, 5, hour, 0).astimezone(), group_ids=[201])
    assert len(outputs) == (0 if period is None else 1)
    assert calls == ([] if period is None else [(period, [])])
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_hourly_recent_copy_is_persisted_only_after_confirmed_delivery(tools, monkeypatch):
    from types import SimpleNamespace
    tools.domains.set_feature(201, "hourly", True)
    tools.store.set_setting("hourly_enabled", True)
    tools.store.set_setting("hourly_start", "00:00")
    tools.store.set_setting("hourly_end", "23:00")
    tools.db.ensure_hourly_delivery("synthetic-import", 201, 77, "已导入报时")
    tools.db.mark_hourly_delivery_sent("synthetic-import", 201)
    calls = []
    def compose(period, blocked):
        calls.append((period, blocked))
        return SimpleNamespace(text="合成报时", fingerprint=100 + len(calls))
    monkeypatch.setattr(tools.hourly, "compose", compose)
    now = datetime(2026, 10, 5, 9, 0).astimezone()
    first = (await tools.tick(now, group_ids=[201]))[0]["result"]
    assert calls == [("morning", [77])]
    tools.mark_delivered(first, False)
    assert tools.store.get_setting("hourly_recent:201", []) == []
    tools.mark_delivered(first, True)
    restarted = ToolExecutor(tools.gateway, tools.root, tools.store)
    monkeypatch.setattr(restarted.hourly, "compose", compose)
    second = (await restarted.tick(now + timedelta(hours=1), group_ids=[201]))[0]["result"]
    assert calls[-1] == ("daytime", [101, 77])
    assert second.data["delivery"]["fingerprint"] == 102
    assert tools.gateway.calls == []
    await restarted.close()
    await tools.close()


@run_async
async def test_game_nickname_replacements_preserve_inline_mentions_and_no_duplicate_fallback(tools):
    outcome = GameEvent(201, "roulette", "合成甲安全，合成乙结束。", "roulette_safe",
        mention_user_ids=(102, 103, 104), mention_replacements=(("合成甲", 102), ("合成乙", 103)))
    result = await tools._game_result(outcome)
    assert result.messages == [{"type": "at", "data": {"qq": "104"}}, {"type": "text", "data": {"text": " "}},
        {"type": "at", "data": {"qq": "102"}}, {"type": "text", "data": {"text": "安全，"}},
        {"type": "at", "data": {"qq": "103"}}, {"type": "text", "data": {"text": "结束。"}}]
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_cursed_guess_declares_original_message_recall_without_qq_writes(tools, monkeypatch):
    monkeypatch.setattr(tools.games, "guess_number", lambda *args: GameEvent(201, "guess", "合成诅咒事件", "guess_cursed"))
    result = await tools.execute(event("#猜 510", eid="123"), ToolCall("guess_submit", {"value": 510}))
    assert result.status == "ok" and result.data["actions"] == [{"action": "delete_msg", "params": {"message_id": 123}}]
    assert tools.gateway.calls == []
    await tools.close()


@run_async
async def test_bili_diagnostics_never_fake_all_or_atall(tools, monkeypatch):
    calls = []
    async def video(uid):
        calls.append(("video", uid))
        return {"url": "https://example.invalid/video", "author": "合成", "text": "合成视频"}
    async def live(uid):
        calls.append(("live", uid))
        return {"url": "https://example.invalid/live", "author": "合成", "text": "合成直播", "live": "0"}
    monkeypatch.setattr(tools.asoul, "fetch_video", video)
    monkeypatch.setattr(tools.asoul, "fetch_live", live)
    result = await tools.execute(event(group=None), ToolCall("bili_test", {"kind": "all", "uid": "123"}))
    assert result.status == "ok" and set(result.data["result"]) >= {"dynamic", "video", "live", "comment"}
    assert calls == [("video", "123"), ("live", "123")]
    result = await tools.execute(event(), ToolCall("bili_test", {"kind": "atall"}))
    assert result.status == "ok" and result.messages[0] == {"type": "at", "data": {"qq": "all"}}
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)


@run_async
async def test_bili_combined_diagnostics_keep_failures_in_backend_and_successes_visible(tools, monkeypatch):
    import httpx
    async def video(uid):
        return {"text": "upstream title includes error and ReadTimeout literally", "url": "https://example.invalid/video"}
    async def live(uid):
        raise httpx.ReadTimeout("synthetic-network-detail")
    async def comments(uid):
        raise RuntimeError("synthetic-comment-detail")
    tools.store.set_setting("bili_comment_target_uids", ["123"])
    monkeypatch.setattr(tools.asoul, "fetch_video", video)
    monkeypatch.setattr(tools.asoul, "fetch_live", live)
    monkeypatch.setattr(tools.asoul, "fetch_dynamics", comments)
    result = await tools.execute(event(group=None), ToolCall("bili_test", {"kind": "all", "uid": "123"}))
    assert result.status == "ok"
    visible = json.loads(result.text)
    assert visible["video"]["text"] == "upstream title includes error and ReadTimeout literally"
    assert set(visible) == {"dynamic", "video"}
    assert result.data["errors"] == {"live": "ReadTimeout", "comment": "RuntimeError"}
    assert result.data["result"]["live"] == {"error": "ReadTimeout"}
    assert "synthetic-network-detail" not in result.text and "RuntimeError" not in result.text
    facts = result.data["facts"]
    assert facts["successful_items"] == ["dynamic", "video"]
    assert "errors" not in facts and "authentication_error" not in facts
    assert "RuntimeError" not in json.dumps(facts, ensure_ascii=False)
    assert "synthetic-network-detail" not in json.dumps(facts, ensure_ascii=False)
    await tools.close()


@run_async
async def test_duplicate_network_failure_remains_technical_error(tools, monkeypatch):
    async def failed_scan(platform, groups, ignore):
        return [], [202]
    monkeypatch.setattr(tools.duplicate, "scan_all", failed_scan)
    result = await tools.execute(event(), ToolCall("duplicate_scan", {"group_ids": [201, 202]}))
    assert result.status == "error" and result.data["failed_groups"] == [202] and not result.messages
    assert tools.corrections.list_entries()[0]["skill_id"] == "duplicate_scan"
    await tools.close()


@run_async
async def test_failed_notification_retries_after_delay_without_render_hot_loop(tools):
    now = datetime.now().astimezone()
    tools.domains.set_feature(201, "bilibili", True)
    with tools.db.connect() as conn:
        conn.execute("INSERT INTO harness_bili_updates VALUES(?,?,?,?,?)", ("retry", "合成B站通知", "text", "null", "[201]"))
    pending = await tools.tick(now, group_ids=[201])
    tools.mark_delivered(pending[0]["result"], False)
    assert not await tools.tick(now + timedelta(seconds=1), group_ids=[201])
    with tools.db.connect() as conn:
        row = conn.execute("SELECT * FROM harness_pending_outputs").fetchone()
        assert row["attempts"] == 1 and row["next_attempt"] > now.timestamp() + 30
    assert len(await tools.tick(now + timedelta(seconds=70), group_ids=[201])) == 1


@run_async
async def test_wife_collective_rounds_advance_and_conclude_once(tools):
    started = datetime.fromisoformat("2026-10-04T10:00:00+08:00")
    members = [{"user_id": user, "nickname": f"合成{user}"} for user in (101, 102, 103)]
    for user in (101, 102, 103):
        tools.wife.record_activity(f"activity:{user}", 201, user, started)
    for user in (101, 103):
        outcome = tools.wife.draw(201, user, f"合成{user}", members, started)
        tools.wife_game.ensure_relation_for_draw(outcome.record)
    for number, clock in ((1, "11:59:30"), (2, "17:59:30"), (3, "23:29:30")):
        now = datetime.fromisoformat("2026-10-04T" + clock + "+08:00")
        pending = await tools.tick(now, group_ids=[201])
        assert len(pending) == 1
        result = pending[0]["result"]
        assert result.data["delivery"]["round"] == number
        assert image_path(result).is_file()
        with tools.db.connect() as conn:
            actions = conn.execute("SELECT COUNT(*) FROM today_wife_interaction_events WHERE group_id=201").fetchone()[0]
        assert await tools.tick(now + timedelta(seconds=1), group_ids=[201]) == []
        with tools.db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM today_wife_interaction_events WHERE group_id=201").fetchone()[0] == actions
        tools.mark_delivered(result, True)
        assert tools.wife_game.collective_round_delivered(201, number, now)
    with tools.db.connect() as conn:
        assert conn.execute("SELECT status FROM today_wife_day_states WHERE group_id=201").fetchone()[0] == "published"


@run_async
async def test_skill_correction_commands_preserve_records_permissions_and_resolution(tools):
    from tangtang_harness.router import route

    text = "#技能纠错 记录 ranking data_stale warning 旧榜单日期不正确 api_key=synthetic-secret"
    call = route(text, event(text)).tools[0]
    denied = await tools.execute(event(text, user=102), call)
    assert denied.status == "denied" and tools.corrections.list_entries() == []
    recorded = await tools.execute(event(text), call)
    entry = recorded.data["entry"]
    assert entry["user_id"] == 101 and entry["group_id"] == 201
    assert entry["source"] == "operator" and "synthetic-secret" not in entry["detail"]
    for command in ("纠错 列表", "纠错 查看 " + entry["id"], "纠错 解决 " + entry["id"], "纠错 导出"):
        denied = await tools.execute(event(user=102), ToolCall("skill_admin", {"text": command}))
        assert denied.status == "denied" and "entries" not in denied.data and "path" not in denied.data
    listed = await tools.execute(event(), ToolCall("skill_admin", {"text": "纠错 列表"}))
    assert entry["id"] in listed.text
    assert listed.data["entries"][0]["id"] == entry["id"]
    categories = await tools.execute(event(), ToolCall("skill_admin", {"text": "纠错 类别"}))
    assert "data_stale" in categories.data["categories"]
    resolved = await tools.execute(event(), ToolCall("skill_admin", {"text": "纠错 解决 " + entry["id"] + " 已修正日期"}))
    assert resolved.data["entry"]["status"] == "resolved"
    assert resolved.data["entry"]["resolution"] == "已修正日期"
    assert not (await tools.execute(event(), ToolCall("skill_admin", {"text": "纠错 列表 open"}))).data["entries"]
    repeated = await tools.execute(event(), ToolCall("skill_admin", {"text": "纠错 解决 " + entry["id"]}))
    assert "已经解决" in repeated.text
    restarted = ToolExecutor(tools.gateway, tools.root, tools.store)
    viewed = await restarted.execute(event(), ToolCall("skill_admin", {"text": "纠错 查看 " + entry["id"]}))
    assert viewed.data["entry"]["status"] == "resolved"
    exported = await restarted.execute(event(), ToolCall("skill_admin", {"text": "纠错 导出"}))
    payload = json.loads(Path(exported.data["path"]).read_text(encoding="utf-8"))
    assert payload[0]["id"] == entry["id"] and payload[0]["resolution"] == "已修正日期"
    assert "synthetic-secret" not in json.dumps(payload)


@run_async
async def test_actual_tool_failure_is_queryable_exportable_and_scoped_cleanup(tools):
    failed = await tools.execute(event(user=102), ToolCall("guess_submit", {"value": "invalid"}))
    assert failed.status == "error"
    entry = tools.corrections.list_entries()[0]
    assert entry["category"] == "parameter_error" and entry["skill_id"] == "guess_submit"
    assert entry["user_id"] == 102 and entry["source"] == "skill_invocation"
    tools.corrections.record(skill_id="ranking", category="data_stale", severity="info",
                             user_id=101, detail="保留另一用户记录")
    exported = await tools.execute(event(), ToolCall("skill_admin", {"text": "数据 导出"}))
    assert exported.data["corrections_count"] == 2
    assert len(json.loads(Path(exported.data["corrections_path"]).read_text(encoding="utf-8"))) == 2
    denied = await tools.execute(event(user=102), ToolCall("skill_admin", {"text": "数据 清理 102"}))
    assert denied.status == "denied" and tools.corrections.get(entry["id"])
    removed = await tools.execute(event(), ToolCall("skill_admin", {"text": "数据 清理 102"}))
    assert removed.data["corrections_removed"] == 1 and removed.data["removed"] == 1
    assert tools.corrections.get(entry["id"]) is None
    assert tools.corrections.list_entries()[0]["user_id"] == 101
    status = await tools.execute(event(), ToolCall("skill_admin", {"text": "状态"}))
    assert status.data["corrections"]["by_status"] == {"open": 1}


def stub_qr_login(monkeypatch, tools, *, outcome="done"):
    from types import SimpleNamespace
    from tangtang_harness.business import asoul

    class Picture:
        def to_file(self, filename):
            Image.new("RGB", (24, 24), "white").save(filename)

    class Login:
        def get_qrcode_picture(self):
            return Picture()

        async def check_state(self):
            if outcome == "error":
                raise RuntimeError("provider failed token=synthetic-secret")
            return outcome

        def get_credential(self):
            return SimpleNamespace(get_cookies=lambda: {"SESSDATA": "synthetic-cookie", "bili_jct": "synthetic-csrf"})

    async def create():
        return Login()

    monkeypatch.setattr(asoul, "login_v2", SimpleNamespace(QrCodeLoginEvents=SimpleNamespace(DONE="done", TIMEOUT="timeout")))
    monkeypatch.setattr(tools.asoul, "create_qr_login", create)


@run_async
async def test_qr_success_notification_survives_restart_scopes_private_and_retries(tools, monkeypatch):
    stub_qr_login(monkeypatch, tools)
    result = await tools.execute(event(group=None), ToolCall("bili_login", {}))
    assert result.status == "ok" and image_path(result).is_file()
    await asyncio.gather(*tools._qr_tasks)
    assert tools.db.asoul_state("bilibili_credential")["sessdata"] == "synthetic-cookie"
    restarted = ToolExecutor(tools.gateway, tools.root, tools.store)
    now = datetime.now().astimezone()
    assert await restarted.tick(now, group_ids=[], user_ids=[102]) == []
    outputs = await restarted.tick(now, group_ids=[], user_ids=[101])
    assert len(outputs) == 1
    item = outputs[0]
    assert item["group_id"] is None and item["user_id"] == 101 and item["self_id"] == 999
    assert "登录成功" in item["result"].text
    restarted.mark_delivered(item["result"], False)
    assert await restarted.tick(now + timedelta(seconds=1), group_ids=[], user_ids=[101]) == []
    retried = await restarted.tick(now + timedelta(seconds=70), group_ids=[], user_ids=[101])
    assert len(retried) == 1 and retried[0]["result"].messages == item["result"].messages
    restarted.mark_delivered(retried[0]["result"], True)
    assert await restarted.tick(now + timedelta(seconds=140), group_ids=[], user_ids=[101]) == []
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)


@run_async
async def test_qr_notification_empty_private_allowlist_means_all_private_users(tools, monkeypatch):
    stub_qr_login(monkeypatch, tools)
    await tools.execute(event(group=None), ToolCall("bili_login", {}))
    await asyncio.gather(*tools._qr_tasks)
    outputs = await tools.tick(group_ids=[], user_ids=[])
    assert len(outputs) == 1
    assert outputs[0]["group_id"] is None and outputs[0]["user_id"] == 101
    assert "登录成功" in outputs[0]["result"].text
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)
    await tools.close()


@pytest.mark.parametrize("outcome,text", [("timeout", "已过期"), ("error", "登录失败")])
@run_async
async def test_qr_expiration_is_business_feedback_and_provider_failure_is_technical(tools, monkeypatch, outcome, text):
    stub_qr_login(monkeypatch, tools, outcome=outcome)
    denied = await tools.execute(event(group=None, user=102), ToolCall("bili_login", {}))
    assert denied.status == "denied" and not tools._qr_tasks
    await tools.execute(event(group=None), ToolCall("bili_login", {}))
    await asyncio.gather(*tools._qr_tasks)
    outputs = await tools.tick(group_ids=[], user_ids=[101])
    assert len(outputs) == 1 and text in outputs[0]["result"].text
    assert outputs[0]["result"].status == ("error" if outcome == "error" else "clarification")
    assert not tools.db.asoul_state("bilibili_credential", {})
    if outcome == "error":
        entry = tools.corrections.list_entries()[0]
        assert entry["skill_id"] == "bili_login" and "synthetic-secret" not in entry["detail"]


@pytest.mark.parametrize('authenticated,phrase', [(True, '已登录（已验证）'), (False, '登录已失效')])
@run_async
async def test_bili_status_verifies_saved_cookie_instead_of_claiming_it_is_logged_in(tools, monkeypatch, authenticated, phrase):
    from types import SimpleNamespace
    async def check_valid():
        return authenticated
    monkeypatch.setattr(tools.asoul, '_credential', lambda: SimpleNamespace(check_valid=check_valid))
    result = await tools.execute(event(group=None), ToolCall('bili_status', {}))
    assert result.data['authenticated'] is authenticated and phrase in result.text
    assert not any(action.startswith('send_') for action, _ in tools.gateway.calls)


@run_async
async def test_bili_status_keeps_failed_authentication_check_unknown(tools, monkeypatch):
    from types import SimpleNamespace
    async def check_valid():
        raise RuntimeError('synthetic-credential-sentinel')
    monkeypatch.setattr(tools.asoul, '_credential', lambda: SimpleNamespace(check_valid=check_valid))
    monkeypatch.setattr(tools.asoul, 'credential_available', lambda: True)
    result = await tools.execute(event(group=None), ToolCall('bili_status', {}))
    assert result.data['authenticated'] is None
    assert '有效性未验证' in result.text and 'RuntimeError' not in result.text
    assert result.data['authentication_error'] == 'RuntimeError'
    assert 'synthetic-credential-sentinel' not in result.text
    assert result.data['facts']['authenticated'] is None
    assert result.data['facts']['credential_available'] is True
    assert 'authentication_error' not in result.data['facts']
    assert 'RuntimeError' not in json.dumps(result.data['facts'], ensure_ascii=False)


@run_async
async def test_full_help_image_text_and_admin_manual_are_distinct(tools):
    from types import SimpleNamespace
    from tangtang_harness.router import route

    async def command(text, user=101):
        return await tools.execute(event(text, user=user), route(text, event(text, user=user)).tools[0])

    card = await command("#帮助", user=102)
    assert card.status == "ok" and card.data["format"] == "image"
    assert card.data["category_count"] >= 5
    with Image.open(image_path(card)) as image:
        assert image.width == tools.renderer.WIDTH and image.height > 1500
    text = await command("#帮助文字", user=102)
    assert text.data["format"] == "text" and not text.messages
    content = text.data["forward_nodes"][0]["data"]["content"][0]
    assert content["type"] == "text"
    assert all(command in content["data"]["text"] for command in ("#装弹成语", "#群设置", "#发言排行", "#ww登录", "#nte查询"))
    denied = await command("#管理员帮助", user=102)
    assert denied.status == "denied" and "forward_nodes" not in denied.data
    admin = await command("#超级管理员帮助")
    assert admin.status == "ok" and admin.data["page_count"] == 4
    nodes = admin.data["forward_nodes"]
    assert len(nodes) == 8
    assert [node["data"]["content"][0]["type"] for node in nodes] == ["image", "text"] * 4
    for node in nodes[::2]:
        assert image_path(SimpleNamespace(messages=node["data"]["content"])).is_file()
    manual = "\n".join(node["data"]["content"][0]["data"]["text"] for node in nodes[1::2])
    assert all(text in manual for text in ("权限与本群设置", "系统与集群", "统计与游戏接口", "公告与运维", "#技能 纠错"))
    assert "NoneBot" not in manual and "TangtangHarness" in manual
    assert not any(action.startswith("send_") for action, _ in tools.gateway.calls)


@run_async
async def test_routed_passive_group_switches_and_probabilities_change_real_behavior(tools):
    from tangtang_harness.router import route

    async def configure(parameter, value):
        text = f"#系统设置 被动互动 201 {parameter} {value}"
        source = event(text, group=None)
        result = await tools.execute(source, route(text, source).tools[0])
        assert result.status == "ok", result.text
        return result.data["config"]

    for parameter, key in (("表情", "reaction_enabled"), ("复读", "repeat_enabled"), ("三连", "triple_enabled")):
        assert (await configure(parameter, "开"))[key] is True
        assert (await configure(parameter, "关"))[key] is False
    for raw, expected in (("0%", 0), ("1%", .01), ("50%", .5), ("100%", 1), ("0", 0), ("0.25", .25), ("1", 1)):
        assert (await configure("表情概率", raw))["reaction_probability"] == expected
    assert tools.store.get_setting("passive:202", {}) == {}
    assert tools.store.get_setting("被动互动", None) is None
    tools.domains.set_feature(201, "passive_interaction", True)
    await configure("表情", "开")
    reacting = await tools.collect(event("合成互动", user=102, eid="11"), passive=True)
    assert reacting.data["actions"][0]["action"] == "set_msg_emoji_like"
    await configure("表情", "关")
    assert await tools.collect(event("停止表情", user=102, eid="12"), passive=True) is None
    source = event("#系统设置 被动互动 201 状态", group=None)
    status = await tools.execute(source, route(source.text, source).tools[0])
    assert status.data["config"]["reaction_enabled"] is False
