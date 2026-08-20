from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

import bot.plugins.asoul as plugin


class FakeBot:
    self_id = 3987707335


class _ScheduleFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.finished_message: str | None = None

    async def finish(self, message: str) -> None:
        self.finished_message = message
        raise _ScheduleFinished()


def test_schedule_reply_ignores_a_option(monkeypatch):
    matcher = FakeMatcher()
    captured = {}

    async def fake_schedule_for_day(day):
        captured["day"] = day
        return []

    def fake_render_schedule(day, title, items):
        return "完整日程：心宜 / 思诺"

    def fail_card(*args, **kwargs):
        raise RuntimeError("card unavailable")

    monkeypatch.setattr(plugin.service, "schedule_for_day", fake_schedule_for_day)
    monkeypatch.setattr(plugin.service, "render_schedule", fake_render_schedule)
    monkeypatch.setattr(plugin.renderer, "render_schedule", fail_card)

    with pytest.raises(_ScheduleFinished):
        asyncio.run(plugin.finish_schedule_reply(matcher, "今日直播", datetime(2026, 8, 16, 12, 0), "-a"))
    assert captured["day"] == date(2026, 8, 16)
    assert matcher.finished_message == "完整日程：心宜 / 思诺"


def test_week_schedule_reply_ignores_a_option(monkeypatch):
    matcher = FakeMatcher()
    captured = {}

    async def fake_schedule_for_days(first, last):
        captured["first"] = first
        return {first: []}

    def fake_render_schedule(day, title, items):
        return "完整日程：心宜 / 思诺"

    def fail_card(*args, **kwargs):
        raise RuntimeError("card unavailable")

    monkeypatch.setattr(plugin.service, "schedule_for_days", fake_schedule_for_days)
    monkeypatch.setattr(plugin.service, "render_schedule", fake_render_schedule)
    monkeypatch.setattr(plugin.renderer, "render_week_schedule", fail_card)

    with pytest.raises(_ScheduleFinished):
        asyncio.run(plugin.finish_week_schedule_reply(matcher, "-a"))
    assert captured["first"] is not None
    assert "心宜" in matcher.finished_message
    assert "思诺" in matcher.finished_message


def test_live_push_mentions_all_only_when_bot_is_admin(monkeypatch):
    bot = FakeBot()
    calls = []

    async def updates():
        return ["【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1"]

    async def call_api(_, action, **params):
        calls.append((action, params))
        if action == "get_group_member_info":
            return {"data": {"role": "member"}}
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_group_ids=(1067772451,),
        asoul_bili_effective_group_ids=(1067772451,),
        asoul_bili_render_cards=False,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())

    assert [action for action, _ in calls] == ["get_group_member_info", "send_group_msg"]
    assert calls[-1][1]["message"] == "【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1"


def test_live_push_mentions_all_when_bot_is_admin(monkeypatch):
    bot = FakeBot()
    calls = []

    async def updates():
        return ["【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1"]

    async def call_api(_, action, **params):
        calls.append((action, params))
        if action == "get_group_member_info":
            return {"data": {"role": "admin"}}
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_group_ids=(1067772451,),
        asoul_bili_effective_group_ids=(1067772451,),
        asoul_bili_render_cards=False,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())

    assert [action for action, _ in calls] == ["get_group_member_info", "send_group_msg"]
    assert calls[-1][1]["message"][0].type == "at"
    assert calls[-1][1]["message"][0].data["qq"] == "all"


def test_monitor_does_not_poll_before_a_bot_is_connected(monkeypatch):
    async def updates():
        raise AssertionError("Bilibili detection must wait for a connected bot")

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_effective_group_ids=(1067772451,),
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin, "get_bots", lambda: {})

    asyncio.run(plugin._send_monitor_messages())
