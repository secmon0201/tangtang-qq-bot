from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import Message, MessageSegment

nonebot.init()

import bot.plugins.commands as commands
from bot.application.local_features import FeatureRequest
from bot.config import A_COAST_GROUP_IDS


def test_personal_stats_query_accepts_self_qq_and_at_targets():
    assert commands.personal_stats_query(Message(), 42) == (42, "day")
    assert commands.personal_stats_query(Message("月"), 42) == (42, "month")
    assert commands.personal_stats_query(Message("903848042 周"), 42) == (903848042, "week")
    assert commands.personal_stats_query(
        Message([MessageSegment.at("903848042"), MessageSegment.text(" 总")]), 42
    ) == (903848042, "total")


def test_personal_stats_query_rejects_ambiguous_arguments():
    assert commands.personal_stats_query(Message("903848042 月 额外"), 42) is None
    assert commands.personal_stats_query(
        Message([MessageSegment.at("903848042"), MessageSegment.at("123456789")]), 42
    ) is None


def test_personal_stats_target_path_reaches_image_delivery(monkeypatch):
    delivered: list[tuple[str, object]] = []

    class StatsService:
        def enabled_groups(self):
            return frozenset({1001})

        def personal_group_totals(self, target_user_id: int, scope: str):
            assert (target_user_id, scope) == (903848042, "total")
            return [{"group_id": 1001, "group_name": "海岸一群", "message_count": 7}]

    async def no_avatars(rows):
        return {}

    async def no_group_avatars(rows):
        return {}

    async def record_delivery(matcher, fallback, render, *, prefix=None):
        delivered.append((fallback, prefix))
        render()

    monkeypatch.setattr(commands, "stats_service", StatsService())
    monkeypatch.setattr(commands, "current_group", lambda event: 1001)
    monkeypatch.setattr(commands, "cached_avatar_paths", no_avatars)
    monkeypatch.setattr(commands, "group_avatar_paths", no_group_avatars)
    monkeypatch.setattr(commands, "finish_with_image_or_text", record_delivery)
    monkeypatch.setattr(
        commands.report_renderer,
        "render_personal_message_stats",
        lambda *args: None,
    )
    event = SimpleNamespace(user_id=42, message=Message([MessageSegment.at("903848042")]))

    asyncio.run(
        commands._run_local_personal_stats_feature(
            _Matcher(),
            SimpleNamespace(self_id=2),
            event,
            FeatureRequest("personal_stats", "总", True, "mentioned"),
        )
    )

    assert delivered
    assert "五群合计：7 条" in delivered[0][0]


class _Matcher:
    async def finish(self, message: str) -> None:
        raise AssertionError(f"unexpected command rejection: {message}")


def test_super_admin_can_request_every_a_coast_ranking_scope_in_private_chat(monkeypatch):
    requested: list[tuple[str, int | None]] = []
    rendered_titles: list[str] = []

    class StatsService:
        def ranking_rows(self, scope: str, group_id: int | None):
            requested.append((scope, group_id))
            return []

        def group_totals(self, scope: str):
            return []

        def render_rows(self, rows, title: str) -> str:
            return title

    async def no_avatars(rows):
        return {}

    async def fail_web_render(payload):
        raise RuntimeError("web renderer unavailable")

    async def record_response(matcher, fallback, render, *, prefix=None):
        rendered_titles.append(fallback)

    monkeypatch.setattr(commands, "is_super_admin", lambda user_id: user_id == 42)
    monkeypatch.setattr(commands, "stats_service", StatsService())
    monkeypatch.setattr(commands, "cached_avatar_paths", no_avatars)
    monkeypatch.setattr(commands, "finish_with_image_or_text", record_response)
    monkeypatch.setattr(commands.community_web_renderer, "render_ranking", fail_web_render)
    event = SimpleNamespace(user_id=42)

    for argument in ("日", "周", "月", "总"):
        asyncio.run(
            commands.finish_message_ranking(_Matcher(), event, Message(argument), a_coast=True)
        )

    assert requested == [("day", None), ("week", None), ("month", None), ("total", None)]
    assert rendered_titles == [
        "A海岸今日灌水王\n记录自 2026-07-28 起",
        "A海岸本周灌水王\n记录自 2026-07-28 起",
        "A海岸本月灌水王\n记录自 2026-07-28 起",
        "A海岸传奇灌水王\n记录自 2026-07-28 起",
    ]


def test_ranking_uses_web_capture_and_appends_the_shared_short_link(monkeypatch, tmp_path):
    delivered: list[Message] = []

    class StatsService:
        def ranking_rows(self, scope: str, group_id: int | None):
            assert (scope, group_id) == ("day", None)
            return [{"rank": 1, "nickname": "测试成员", "message_count": 7, "group_name": "修会"}]

        def render_rows(self, rows, title: str) -> str:
            raise AssertionError("HTML screenshot should avoid the Pillow fallback")

        def group_totals(self, scope: str):
            return [
                {"group_id": group_id, "group_name": str(group_id), "message_count": 1}
                for group_id in A_COAST_GROUP_IDS
            ]

    async def no_avatars(rows):
        return {}

    async def no_group_avatars(rows):
        return {}

    async def render_web(payload):
        assert payload["scope"] == "day"
        assert payload["group"] == "a-coast"
        assert payload["chart"]["kind"] == "group"
        assert len(payload["chart"]["rows"]) == 5
        image = tmp_path / "ranking.png"
        image.write_bytes(b"png")
        return image

    class Matcher:
        async def finish(self, message):
            delivered.append(message)

    monkeypatch.setattr(commands, "stats_service", StatsService())
    monkeypatch.setattr(commands, "current_group", lambda event: None)
    monkeypatch.setattr(commands, "is_super_admin", lambda user_id: user_id == 42)
    monkeypatch.setattr(commands, "user_report_rows", lambda rows: list(rows))
    monkeypatch.setattr(commands, "cached_avatar_paths", no_avatars)
    monkeypatch.setattr(commands, "group_avatar_paths", no_group_avatars)
    monkeypatch.setattr(commands.community_web_renderer, "render_ranking", render_web)

    asyncio.run(
        commands.finish_message_ranking(
            Matcher(), SimpleNamespace(user_id=42), Message("日"), a_coast=True
        )
    )

    assert len(delivered) == 1
    assert "在线：s.secmon.cn/s" in str(delivered[0])
    assert delivered[0][0].type == "image"
