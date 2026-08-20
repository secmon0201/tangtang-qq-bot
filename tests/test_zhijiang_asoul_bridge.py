from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import nonebot
import pytest


class _Finished(Exception):
    pass


class _Matcher:
    def __init__(self) -> None:
        self.messages: list[object] = []

    async def send(self, message: object) -> None:
        self.messages.append(message)

    async def finish(self, message: object | None = None) -> None:
        if message is not None:
            self.messages.append(message)
        raise _Finished


def test_zhijiang_week_command_uses_asoul_calendar(monkeypatch, tmp_path):
    nonebot.init()
    from bot.plugins import zhijiang

    captured: dict[str, object] = {}

    async def schedule_for_days(first, last):
        captured["range"] = (first, last)
        return {first: [SimpleNamespace()]}

    async def render_week_schedule(days):
        captured["days"] = list(days)
        path = tmp_path / "schedule.png"
        path.write_bytes(b"test")
        return path

    monkeypatch.setattr(zhijiang.asoul_schedule, "schedule_for_days", schedule_for_days)
    monkeypatch.setattr(zhijiang.asoul_schedule, "render_schedule", lambda *_: "shared calendar")
    monkeypatch.setattr(zhijiang.asoul_renderer, "render_week_schedule", render_week_schedule)
    monkeypatch.setattr(zhijiang, "_now", lambda: datetime(2026, 7, 27, tzinfo=zhijiang.ZoneInfo("Asia/Shanghai")))
    monkeypatch.setattr(zhijiang, "settings", SimpleNamespace(report_output_mode="local_image"))

    matcher = _Matcher()
    with pytest.raises(_Finished):
        asyncio.run(zhijiang._finish_with_asoul_week_schedule(matcher))

    first, last = captured["range"]
    assert first.isoformat() == "2026-07-27"
    assert last.isoformat() == "2026-08-02"
    assert captured["days"] == [(first, [SimpleNamespace()])] + [
        (first.fromordinal(first.toordinal() + offset), []) for offset in range(1, 7)
    ]
    assert len(matcher.messages) == 1
