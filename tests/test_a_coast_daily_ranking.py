from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.services.a_coast_daily_ranking import ACoastDailyRankingDeliveryService, TITLE
import bot.services.a_coast_daily_ranking as delivery_module


ZONE = ZoneInfo("Asia/Shanghai")


class _Stats:
    def ranking_rows(self, scope: str):
        assert scope == "day"
        return [{"rank": 1, "user_id": 7, "nickname": "成员", "message_count": 12}]

    def group_totals(self, scope: str):
        assert scope == "day"
        return [
            {"group_id": 1001, "group_name": "group one", "message_count": 8},
            {"group_id": 1002, "group_name": "group two", "message_count": 4},
        ]

    def render_rows(self, rows, title: str) -> str:
        assert title == TITLE
        return "text fallback"


class _Avatars:
    async def prefetch(self, rows):
        return {}


class _Renderer:
    def __init__(self) -> None:
        self.calls = []

    def render_ranking(self, *args, **kwargs) -> Path:
        self.calls.append((args, kwargs))
        return Path("ranking.png")


def _service(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    renderer = _Renderer()
    return db, renderer, ACoastDailyRankingDeliveryService(
        db, _Stats(), _Avatars(), renderer, group_ids=(1001, 1002)
    )


def test_daily_ranking_delivery_posts_one_image_per_group_and_is_idempotent(monkeypatch, tmp_path):
    db, renderer, service = _service(tmp_path)
    sent = []

    async def send(bot, action, **params):
        sent.append((action, params))

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(delivery_module, "call_qq_action", send)
    now = datetime(2026, 7, 28, 23, 50, tzinfo=ZONE)

    result = asyncio.run(service.deliver_once(object(), now))
    repeated = asyncio.run(service.deliver_once(object(), now))

    assert result == {"status": "processed", "sent": 2, "failed": 0, "uncertain": 0}
    assert repeated == {"status": "already_delivered", "sent": 0, "failed": 0}
    assert [params["group_id"] for _, params in sent] == [1001, 1002]
    assert all(params["message"] == "image:ranking.png" for _, params in sent)
    assert renderer.calls[0][0][1] == TITLE
    assert renderer.calls[0][1]["show_group_labels"] is True
    assert renderer.calls[0][1]["group_totals"][0]["message_count"] == 8
    assert [row["status"] for row in db.a_coast_daily_ranking_deliveries(now.date())] == ["sent", "sent"]


def test_daily_ranking_delivery_retries_only_failed_groups_within_window(monkeypatch, tmp_path):
    db, _renderer, service = _service(tmp_path)
    attempts = []

    async def send(bot, action, **params):
        group_id = params["group_id"]
        attempts.append(group_id)
        if group_id == 1002 and attempts.count(group_id) == 1:
            raise RuntimeError("temporary disconnect")

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(delivery_module, "call_qq_action", send)
    now = datetime(2026, 7, 28, 23, 55, tzinfo=ZONE)

    first = asyncio.run(service.deliver_once(object(), now))
    second = asyncio.run(service.deliver_once(object(), now))

    assert first == {"status": "processed", "sent": 1, "failed": 1, "uncertain": 0}
    assert second == {"status": "processed", "sent": 1, "failed": 0, "uncertain": 0}
    assert attempts == [1001, 1002, 1002]
    assert [row["status"] for row in db.a_coast_daily_ranking_deliveries(now.date())] == ["sent", "sent"]


def test_daily_ranking_delivery_does_not_create_work_outside_the_late_night_window(tmp_path):
    db, _renderer, service = _service(tmp_path)
    now = datetime(2026, 7, 28, 23, 49, 59, tzinfo=ZONE)

    result = asyncio.run(service.deliver_once(object(), now))

    assert result == {"status": "outside_window", "sent": 0, "failed": 0}
    assert db.a_coast_daily_ranking_deliveries(now.date()) == []
