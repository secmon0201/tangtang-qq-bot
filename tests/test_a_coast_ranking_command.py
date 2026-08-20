from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot
from nonebot.adapters.onebot.v11 import Message

nonebot.init()

import bot.plugins.commands as commands


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

    async def record_response(matcher, fallback, render, *, prefix=None):
        rendered_titles.append(fallback)

    monkeypatch.setattr(commands, "is_super_admin", lambda user_id: user_id == 42)
    monkeypatch.setattr(commands, "stats_service", StatsService())
    monkeypatch.setattr(commands, "cached_avatar_paths", no_avatars)
    monkeypatch.setattr(commands, "finish_with_image_or_text", record_response)
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
