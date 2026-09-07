from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

import bot.plugins.asoul as plugin


class FakeBot:
    self_id = 920000004


class _ScheduleFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.finished_message: str | None = None

    async def finish(self, message: str) -> None:
        self.finished_message = message
        raise _ScheduleFinished()


def enable_bilibili_groups(monkeypatch, *group_ids: int) -> None:
    monkeypatch.setattr(
        plugin,
        "group_domains",
        lambda: SimpleNamespace(enabled_groups=lambda feature: frozenset(group_ids)),
    )


def test_schedule_web_payload_selects_only_requested_view(monkeypatch):
    captured = {}

    async def fake_schedule_for_days(first, last):
        captured["range"] = (first, last)
        return {first: []}

    monkeypatch.setattr(plugin.service, "schedule_for_days", fake_schedule_for_days)

    payload = asyncio.run(plugin._schedule_web_payload("tomorrow"))

    assert payload["view"] == "tomorrow"
    assert len(payload["days"]) == 1
    assert captured["range"][1] >= captured["range"][0]


def test_public_schedule_api_embeds_host_matched_stickers(monkeypatch):
    payload = {"view": "week", "days": [{"items": [{"hosts": ["心宜"]}]}]}

    async def fake_payload(view):
        assert view == "week"
        return payload

    def localize(value):
        assert value is payload
        return {"view": "week", "days": [{"items": [{"sticker_url": "data:image/png;base64,test"}]}]}

    monkeypatch.setattr(plugin, "_schedule_web_payload", fake_payload)
    monkeypatch.setattr(plugin.web_renderer, "localize_schedule_stickers", localize)

    result = asyncio.run(plugin.asoul_live_web_schedule("week"))

    assert result["days"][0]["items"][0]["sticker_url"].startswith("data:image/png;base64,")


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
    monkeypatch.setattr(plugin.web_renderer, "render_schedule", fail_card)
    monkeypatch.setattr(plugin.renderer, "render_schedule", fail_card)

    with pytest.raises(_ScheduleFinished):
        asyncio.run(plugin.finish_schedule_reply(matcher, "今日直播", datetime(2026, 8, 16, 12, 0), "-a"))
    assert captured["day"] == date(2026, 8, 16)
    assert matcher.finished_message == "完整日程：心宜 / 思诺"


def test_schedule_image_reply_uses_the_single_bare_short_link(monkeypatch, tmp_path):
    matcher = FakeMatcher()
    card = tmp_path / "schedule.png"
    card.write_bytes(b"png")

    async def fake_schedule_for_day(day):
        return []

    async def fake_render_schedule(*args, **kwargs):
        return card

    monkeypatch.setattr(plugin.service, "schedule_for_day", fake_schedule_for_day)
    monkeypatch.setattr(plugin.web_renderer, "render_schedule", fake_render_schedule)

    with pytest.raises(_ScheduleFinished):
        asyncio.run(plugin.finish_schedule_reply(matcher, "今日直播", datetime(2026, 8, 16, 12, 0)))

    rendered = str(matcher.finished_message)
    assert rendered.endswith("线上：short.example.invalid/r")
    assert "https://" not in rendered


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
    monkeypatch.setattr(plugin.web_renderer, "render_schedule", fail_card)
    monkeypatch.setattr(plugin.renderer, "render_week_schedule", fail_card)

    with pytest.raises(_ScheduleFinished):
        asyncio.run(plugin.finish_week_schedule_reply(matcher, "-a"))
    assert captured["first"] is not None
    assert "心宜" in matcher.finished_message
    assert "思诺" in matcher.finished_message


def test_live_push_mentions_all_only_when_bot_is_admin(monkeypatch):
    bot = FakeBot()
    calls = []
    enable_bilibili_groups(monkeypatch, 920000001)

    async def updates():
        return ["【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1"]

    async def call_api(_, action, **params):
        calls.append((action, params))
        if action == "get_group_member_info":
            return {"data": {"role": "member"}}
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_group_ids=(920000001,),
        asoul_bili_effective_group_ids=(920000001,),
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
    enable_bilibili_groups(monkeypatch, 920000001)

    async def updates():
        return ["【开播】测试UP\n直播标题\nhttps://live.bilibili.com/1"]

    async def call_api(_, action, **params):
        calls.append((action, params))
        if action == "get_group_member_info":
            return {"data": {"role": "admin"}}
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_group_ids=(920000001,),
        asoul_bili_effective_group_ids=(920000001,),
        asoul_bili_render_cards=False,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())

    assert [action for action, _ in calls] == ["get_group_member_info", "send_group_msg"]
    assert calls[-1][1]["message"][0].type == "at"
    assert calls[-1][1]["message"][0].data["qq"] == "all"


def test_monitor_uses_05a_html_cards_for_dynamic_video_live_and_comment(monkeypatch, tmp_path):
    bot = FakeBot()
    calls = []
    rendered = []
    enable_bilibili_groups(monkeypatch, 920000001)
    card = tmp_path / "05a.png"
    card.write_bytes(b"png")
    messages = [
        "【B站新动态】测试UP\n动态正文\nhttps://example.test/dynamic",
        "【B站新视频】测试UP\n视频标题\nhttps://example.test/video",
        "【开播】测试UP\n直播标题\nhttps://example.test/live",
        "【B站评论区回复】测试UP\n在其他UP的动态底下的回复\n[暗中观察]评论正文\nhttps://example.test/comment",
    ]
    details = {
        messages[0]: {"author": "测试UP", "text": "动态正文", "likes": "100", "following": "20", "followers": "300"},
        messages[1]: {"author": "测试UP", "text": "视频标题", "likes": "101", "following": "21", "followers": "301"},
        messages[2]: {"phase": "start", "author": "测试UP", "text": "直播标题", "likes": "102", "following": "22", "followers": "302"},
        messages[3]: {
            "author": "测试UP",
            "context": "在其他UP的动态底下的回复",
            "text": "[暗中观察]评论正文",
            "rich_nodes": (
                '[{"type":"emoji","text":"[暗中观察]",'
                '"url":"https://example.test/emote.png"},{"type":"text","text":"评论正文"}]'
            ),
            "url": "https://example.test/comment",
        },
    }

    async def updates():
        return messages

    async def render_notification(
        message,
        *,
        live=None,
        dynamic=None,
        video=None,
        comment=None,
        require_media=False,
    ):
        rendered.append((message, live, dynamic, video, comment, require_media))
        return card

    async def call_api(_, action, **params):
        calls.append((action, params))
        if action == "get_group_member_info":
            return {"data": {"role": "member"}}
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_effective_group_ids=(920000001,),
        asoul_bili_render_cards=True,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin.service, "dynamic_notification_details", lambda message: details.get(message) if message.startswith("【B站新动态】") else None)
    monkeypatch.setattr(plugin.service, "video_notification_details", lambda message: details.get(message) if message.startswith("【B站新视频】") else None)
    monkeypatch.setattr(plugin.service, "live_notification_details", lambda message: details.get(message) if message.startswith("【开播】") else None)
    monkeypatch.setattr(plugin.service, "comment_notification_details", lambda message: details.get(message) if message.startswith("【B站评论区回复】") else None)
    monkeypatch.setattr(plugin.web_renderer, "render_notification", render_notification)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())

    assert [(dynamic is not None, video is not None, live is not None, comment is not None) for _, live, dynamic, video, comment, _ in rendered] == [
        (True, False, False, False),
        (False, True, False, False),
        (False, False, True, False),
        (False, False, False, True),
    ]
    assert [rendered[index][slot]["likes"] for index, slot in ((0, 2), (1, 3), (2, 1))] == ["100", "101", "102"]
    assert rendered[3][4]["rich_nodes"].startswith('[{"type":"emoji"')
    assert all(row[5] is True for row in rendered)
    assert [action for action, _ in calls] == [
        "send_group_msg",
        "send_group_msg",
        "get_group_member_info",
        "send_group_msg",
        "send_group_msg",
    ]


def test_monitor_renders_one_card_for_all_target_groups(monkeypatch, tmp_path):
    bot = FakeBot()
    calls = []
    rendered = []
    groups = (920000001, 1067772452)
    enable_bilibili_groups(monkeypatch, *groups)
    monkeypatch.setattr(plugin, "_pending_monitor_messages", {})
    card = tmp_path / "shared.png"
    card.write_bytes(b"png")
    message = "【B站新视频】测试UP\n视频标题\nhttps://example.test/video"
    details = {
        "author": "测试UP",
        "text": "视频标题",
        "url": "https://example.test/video",
        "avatar_url": "https://example.test/avatar.png",
        "cover_url": "https://example.test/cover.png",
    }

    async def render_notification(*args, **kwargs):
        rendered.append((args, kwargs))
        return card

    async def call_api(_, action, **params):
        calls.append((action, params))
        return {"message_id": 1}

    async def updates():
        return [message]

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_render_cards=True,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin.service, "dynamic_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.service, "video_notification_details", lambda _: details)
    monkeypatch.setattr(plugin.service, "live_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.service, "comment_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.web_renderer, "render_notification", render_notification)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())

    assert len(rendered) == 1
    assert rendered[0][1]["require_media"] is True
    assert [params["group_id"] for action, params in calls if action == "send_group_msg"] == list(groups)


def test_monitor_defers_incomplete_media_until_a_later_poll(monkeypatch, tmp_path):
    bot = FakeBot()
    calls = []
    attempts = []
    enable_bilibili_groups(monkeypatch, 920000001)
    monkeypatch.setattr(plugin, "_pending_monitor_messages", {})
    card = tmp_path / "recovered.png"
    card.write_bytes(b"png")
    message = "【B站新视频】测试UP\n视频标题\nhttps://example.test/video"
    details = {
        "author": "测试UP",
        "text": "视频标题",
        "url": "https://example.test/video",
        "avatar_url": "https://example.test/avatar.png",
        "cover_url": "https://example.test/cover.png",
    }
    update_batches = [[message], []]

    async def updates():
        return update_batches.pop(0)

    async def render_notification(*_args, **kwargs):
        attempts.append(kwargs["require_media"])
        if len(attempts) == 1:
            raise plugin.NotificationMediaUnavailable(("avatar_url", "cover_url"))
        return card

    async def call_api(_, action, **params):
        calls.append((action, params))
        return {"message_id": 1}

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_render_cards=True,
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin.service, "dynamic_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.service, "video_notification_details", lambda _: details)
    monkeypatch.setattr(plugin.service, "live_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.service, "comment_notification_details", lambda _: None)
    monkeypatch.setattr(plugin.web_renderer, "render_notification", render_notification)
    monkeypatch.setattr(plugin, "get_bots", lambda: {str(bot.self_id): bot})
    monkeypatch.setattr(plugin, "call_qq_action", call_api)

    asyncio.run(plugin._send_monitor_messages())
    assert calls == []
    assert message in plugin._pending_monitor_messages

    asyncio.run(plugin._send_monitor_messages())
    assert attempts == [True, True]
    assert [action for action, _ in calls] == ["send_group_msg"]
    assert plugin._pending_monitor_messages == {}


def test_monitor_does_not_poll_before_a_bot_is_connected(monkeypatch):
    enable_bilibili_groups(monkeypatch, 920000001)
    async def updates():
        raise AssertionError("Bilibili detection must wait for a connected bot")

    monkeypatch.setattr(plugin, "settings", SimpleNamespace(
        asoul_bili_enabled=True,
        asoul_bili_effective_group_ids=(920000001,),
    ))
    monkeypatch.setattr(plugin.service, "poll_updates", updates)
    monkeypatch.setattr(plugin, "get_bots", lambda: {})

    asyncio.run(plugin._send_monitor_messages())
