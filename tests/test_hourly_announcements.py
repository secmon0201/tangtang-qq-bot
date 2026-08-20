from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from bot.db import Database
from bot.services.hourly_announcements import HourlyAnnouncementService
from nonebot.adapters.onebot.v11.exception import ActionFailed


def make_service(tmp_path, groups=(1001, 1002), texts=("文案甲", "文案乙")):
    db = Database(tmp_path / "bot.db")
    db.configure_groups(groups)
    service = HourlyAnnouncementService(
        db,
        lambda: groups,
        texts=texts,
    )
    service.set_enabled(True)
    service.set_schedule(0, 1439)
    return db, service


def test_hourly_delivery_is_one_per_group_and_slot(tmp_path, monkeypatch):
    db, service = make_service(tmp_path)
    sent = []

    async def fake_paced_call(bot, action, **params):
        sent.append((action, params))

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", fake_paced_call)
    bot = SimpleNamespace()
    now = datetime.fromisoformat("2026-07-20T10:00:00+08:00")

    first = asyncio.run(service.deliver_once(bot, now))
    second = asyncio.run(service.deliver_once(bot, now))

    assert first["sent"] == 2
    assert second["sent"] == 0
    assert len(sent) == 2
    rows = db.hourly_deliveries(service.slot_key(now))
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"sent"}
    assert {row["text_index"] for row in rows} <= {0, 1}


def test_hourly_delivery_avoids_recent_sent_copy_indexes(tmp_path, monkeypatch):
    db, service = make_service(tmp_path, groups=(1001,), texts=("文案甲", "文案乙", "文案丙"))
    sent = []

    async def fake_paced_call(bot, action, **params):
        sent.append(params)

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", fake_paced_call)
    bot = SimpleNamespace()
    for hour in range(10, 13):
        asyncio.run(
            service.deliver_once(
                bot,
                datetime.fromisoformat(f"2026-07-20T{hour:02d}:00:00+08:00"),
            )
        )

    assert len(sent) == 3
    assert len({db.hourly_deliveries(service.slot_key(datetime.fromisoformat(f"2026-07-20T{hour:02d}:00:00+08:00")))[0]["text_index"] for hour in range(10, 13)}) == 3


def test_segmented_copy_is_persisted_across_a_transient_retry(tmp_path, monkeypatch):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    service = HourlyAnnouncementService(db, lambda: (1001,))
    service.set_enabled(True)
    service.set_schedule(0, 1439)
    sent = []
    calls = 0

    async def flaky_send(bot, action, **params):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary failure")
        sent.append(params["message"])

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", flaky_send)
    bot = SimpleNamespace()
    slot = datetime.fromisoformat("2026-07-20T10:00:00+08:00")
    asyncio.run(service.deliver_once(bot, slot))
    pending = db.hourly_deliveries(service.slot_key(slot))[0]

    asyncio.run(service.deliver_once(bot, slot.replace(minute=1)))

    assert pending["message"]
    assert sent == [pending["message"]]


def test_hourly_failure_has_a_finite_retry_limit(tmp_path, monkeypatch):
    db, service = make_service(tmp_path, groups=(1001,), texts=("文案",))
    calls = 0

    async def failing_call(bot, action, **params):
        nonlocal calls
        calls += 1
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", failing_call)
    bot = SimpleNamespace()
    for minute in (0, 1, 2, 3):
        asyncio.run(
            service.deliver_once(
                bot,
                datetime.fromisoformat(f"2026-07-20T10:{minute:02d}:00+08:00"),
            )
        )

    assert calls == service.config.max_attempts
    row = db.hourly_deliveries(service.slot_key(datetime.fromisoformat("2026-07-20T10:00:00+08:00")))[0]
    assert row["status"] == "failed"
    assert row["attempts"] == service.config.max_attempts


def test_hourly_action_failed_is_not_retried_to_avoid_duplicate_posts(tmp_path, monkeypatch):
    db, service = make_service(tmp_path, groups=(1001,), texts=("文案",))
    calls = 0

    async def unconfirmed_send(bot, action, **params):
        nonlocal calls
        calls += 1
        raise ActionFailed(retcode=100, message="send confirmation timed out")

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", unconfirmed_send)
    bot = SimpleNamespace()
    for minute in (0, 1, 2, 3):
        asyncio.run(
            service.deliver_once(
                bot,
                datetime.fromisoformat(f"2026-07-20T10:{minute:02d}:00+08:00"),
            )
        )

    assert calls == 1
    row = db.hourly_deliveries(service.slot_key(datetime.fromisoformat("2026-07-20T10:00:00+08:00")))[0]
    assert row["status"] == "uncertain"
    assert row["attempts"] == 1
    assert row["last_error"] == "ActionFailed"


def test_hourly_does_not_send_outside_configured_time_window(tmp_path, monkeypatch):
    db, service = make_service(tmp_path, groups=(1001,))
    service.set_schedule(9 * 60, 23 * 60)
    sent = []

    async def fake_paced_call(bot, action, **params):
        sent.append(params)

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", fake_paced_call)
    result = asyncio.run(
        service.deliver_once(
            SimpleNamespace(),
            datetime.fromisoformat("2026-07-20T08:00:00+08:00"),
        )
    )

    assert result["status"] == "outside_schedule"
    assert sent == []
    assert db.hourly_deliveries("2026-07-20T08:00+0800") == []


def test_hourly_schedule_supports_cross_midnight_window(tmp_path, monkeypatch):
    _db, service = make_service(tmp_path, groups=(1001,))
    service.set_schedule(22 * 60, 2 * 60)
    sent = []

    async def fake_paced_call(bot, action, **params):
        sent.append(params)

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", fake_paced_call)
    asyncio.run(
        service.deliver_once(
            SimpleNamespace(),
            datetime.fromisoformat("2026-07-21T02:00:00+08:00"),
        )
    )

    assert len(sent) == 1


def test_hourly_night_only_sends_at_configured_hours(tmp_path, monkeypatch):
    _db, service = make_service(tmp_path, groups=(1001,))
    sent = []

    async def fake_paced_call(bot, action, **params):
        sent.append(params)

    monkeypatch.setattr("bot.services.hourly_announcements.call_qq_action", fake_paced_call)
    cases = (
        ("2026-07-20T22:00:00+08:00", True),
        ("2026-07-20T23:00:00+08:00", True),
        ("2026-07-21T00:00:00+08:00", True),
        ("2026-07-21T01:00:00+08:00", False),
        ("2026-07-21T02:00:00+08:00", True),
        ("2026-07-21T03:00:00+08:00", False),
        ("2026-07-21T04:00:00+08:00", True),
        ("2026-07-21T05:00:00+08:00", False),
    )
    for value, should_send in cases:
        result = asyncio.run(service.deliver_once(SimpleNamespace(), datetime.fromisoformat(value)))
        assert (result["sent"] == 1) is should_send

    assert len(sent) == 5


def test_hourly_copy_source_has_segmented_runtime_combinations_and_safe_alias_categories():
    from bot.services.hourly_copy import HourlyCopyCatalog

    root = Path(__file__).parents[1]
    catalog = HourlyCopyCatalog.load(
        root / "bot" / "resources" / "zhijiang_hourly_copy.json",
        root / "bot" / "resources" / "zhijiang_character_aliases.json",
    )
    assert set(catalog.periods) == {"morning", "daytime", "evening", "night"}
    assert all(
        len(catalog.periods[period].segments[segment].texts) >= 100
        for period in catalog.periods
        for segment in ("before", "middle", "after")
    )
    assert sum(catalog.combination_counts().values()) >= 5000
    assert len(catalog.categories["asoul"]) >= 100
    assert "漂泊者" in catalog.categories["other"]
    assert "群友" in catalog.categories["other"]

    all_fragments = [
        fragment
        for period in catalog.periods.values()
        for segment in period.segments.values()
        for fragment in segment.texts
    ]
    assert all("{name}" in fragment for fragment in all_fragments if "{name}" in fragment)
    forbidden = ("矮一姑", "魔丸", "墓岛人", "岛民", "坏女人")
    assert not any(term in alias for term in forbidden for aliases in catalog.categories.values() for alias in aliases)


def test_hourly_status_reports_current_segment_counts(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    service = HourlyAnnouncementService(db, lambda: (1001,))
    service.set_enabled(True)
    service.set_schedule(0, 1439)

    summary = service.schedule_text()

    assert "早112/112/112" in summary
    assert "白112/110/110" in summary
    assert "晚109/107/104" in summary
    assert "夜109/108/103" in summary
    assert "518.56万" in summary
    assert "50/100/50" not in summary


def test_hourly_copy_uses_the_current_period(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    service = HourlyAnnouncementService(
        db,
        lambda: (1001,),
        content_path=Path(__file__).parents[1] / "bot" / "resources" / "zhijiang_hourly_copy.json",
    )

    assert service._period_key(datetime.fromisoformat("2026-07-20T05:59:00+08:00")) == "night"
    assert service._period_key(datetime.fromisoformat("2026-07-20T06:00:00+08:00")) == "morning"
    assert service._period_key(datetime.fromisoformat("2026-07-20T10:00:00+08:00")) == "daytime"
    assert service._period_key(datetime.fromisoformat("2026-07-20T17:00:00+08:00")) == "evening"
    assert service._period_key(datetime.fromisoformat("2026-07-20T22:00:00+08:00")) == "night"
