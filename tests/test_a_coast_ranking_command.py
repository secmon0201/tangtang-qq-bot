from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import Message

nonebot.init()

import bot.plugins.commands as commands
from bot.application.local_features import registered_local_features
from bot.config import A_COAST_GROUP_IDS


def test_personal_message_ranking_is_not_exposed_or_registered():
    help_text = commands.user_help_text()
    for command in (
        "#个人发言统计",
        "#个人发言榜",
        "#个人统计",
        "#我的发言统计",
        "#我的发言榜",
        "#我的统计",
    ):
        assert command not in help_text
    assert not hasattr(commands, "personal_message_stats")
    assert "personal_stats" not in registered_local_features()


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
