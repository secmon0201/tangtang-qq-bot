import asyncio
import ast
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from bot.db import Database
from bot.services.stats import StatsService


class FakeBot:
    self_id = "999"


def test_message_event_counts_only_in_the_fixed_a_coast_scope(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    service = StatsService(db, group_ids=(1001,))
    event = SimpleNamespace(
        group_id=1001,
        user_id=7,
        message_id="message-1",
        time=1784203200,
        sender=SimpleNamespace(card="", nickname="成员 A"),
    )

    assert asyncio.run(service.on_message(FakeBot(), event))
    assert not asyncio.run(service.on_message(FakeBot(), event))
    message_day = datetime.fromtimestamp(event.time, service.zone).date()
    rows = db.today_counts(1001, message_day)
    assert len(rows) == 1
    assert rows[0]["nickname"] == "成员 A"
    assert rows[0]["message_count"] == 1

    outside_event = SimpleNamespace(**{**event.__dict__, "group_id": 1002, "message_id": "outside"})
    assert not asyncio.run(service.on_message(FakeBot(), outside_event))
    assert db.today_counts(1002, message_day) == []


def test_a_coast_ranking_aggregates_groups_and_uses_latest_nickname(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    db.set_group_info(1001, "海岸一群")
    db.set_group_info(1002, "海岸二群")
    first_day = datetime(2026, 7, 20, 10, 0)
    latest_day = datetime(2026, 7, 21, 10, 0)

    for index in range(2):
        assert db.record_message(f"one-{index}", 1001, 7, "旧昵称", first_day)
    for index in range(3):
        assert db.record_message(f"two-{index}", 1002, 7, "新昵称", latest_day)
    for index in range(5):
        assert db.record_message(f"peer-{index}", 1001, 8, "并列成员", latest_day)

    rows = db.message_ranking((1001, 1002), date(2026, 7, 20))

    assert [(row["rank"], row["user_id"], row["message_count"]) for row in rows] == [
        (1, 7, 5),
        (2, 8, 5),
    ]
    assert rows[0]["nickname"] == "新昵称"
    assert rows[0]["group_labels"] == "海岸二群"


def test_bot_outbound_messages_are_counted_only_after_a_confirmed_group_send(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    service = StatsService(db, group_ids=(1001,))
    bot = SimpleNamespace(self_id="999")
    sent_at = datetime(2026, 7, 28, 10, 0, tzinfo=service.zone)

    assert service.record_outbound_success(
        bot, "send_group_msg", {"group_id": 1001}, {"message_id": 55}, sent_at
    )
    assert not service.record_outbound_success(
        bot, "send_group_msg", {"group_id": 1001}, {"message_id": 55}, sent_at
    )
    assert not service.record_outbound_success(
        bot, "send_group_msg", {"group_id": 1002}, {"message_id": 56}, sent_at
    )
    assert not service.record_outbound_success(
        bot, "send_private_msg", {"user_id": 7}, {"message_id": 57}, sent_at
    )

    assert db.today_counts(1001, sent_at.date())[0]["user_id"] == 999
    assert db.today_counts(1001, sent_at.date())[0]["message_count"] == 1


def test_bot_outbound_messages_use_runtime_group_provider(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    service = StatsService(db, group_provider=lambda: (1001,))
    bot = SimpleNamespace(self_id="999")
    sent_at = datetime(2026, 7, 28, 10, 0, tzinfo=service.zone)

    assert service.record_outbound_success(
        bot, "send_group_msg", {"group_id": 1001}, {"message_id": 55}, sent_at
    )
    assert db.today_counts(1001, sent_at.date())[0]["user_id"] == 999


def test_napcat_log_backfill_reads_self_sent_a_coast_messages_from_start_day(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    service = StatsService(db, group_ids=(1001,))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_dir.joinpath("napcat.log").write_text(
        '07-28 [debug] {"stringMsg":{"self_id":999,"user_id":999,"time":1785204000,'
        '"message_id":55,"message_type":"group","sender":{"nickname":"糖糖"},'
        '"post_type":"message_sent","message_sent_type":"self","group_id":1001}}\n'
        '07-28 [debug] {"stringMsg":{"self_id":999,"user_id":999,"time":1785204000,'
        '"message_id":56,"message_type":"group","sender":{"nickname":"糖糖"},'
        '"post_type":"message_sent","message_sent_type":"self","group_id":1002}}\n',
        encoding="utf-8",
    )

    result = service.import_napcat_outbound_logs(log_dir, date(2026, 7, 28))

    assert result == {"files": 1, "matched": 2, "imported": 1, "duplicates": 0, "invalid": 0}
    assert db.today_counts(1001, date(2026, 7, 28))[0]["user_id"] == 999


def test_ranking_windows_are_calendar_day_week_month_and_total():
    today = date(2026, 7, 22)  # Wednesday

    assert StatsService.window_start("day", today) == date(2026, 7, 22)
    assert StatsService.window_start("week", today) == date(2026, 7, 20)
    assert StatsService.window_start("month", today) == date(2026, 7, 1)
    assert StatsService.window_start("total", today) is None


def test_ranking_assigns_tied_members_to_the_first_configured_group(tmp_path):
    db = Database(tmp_path / "bot.db")
    group_ids = (910000101, 910000102, 910000103, 910000104, 910000105)
    db.configure_groups(group_ids)
    db.set_group_info(group_ids[0], "第一海岸群")
    db.set_group_info(group_ids[-1], "第五海岸群")
    stamp = datetime(2026, 7, 20, 10, 0)
    assert db.record_message("first:1", group_ids[0], 7, "成员", stamp)
    assert db.record_message("last:1", group_ids[-1], 7, "成员", stamp)
    service = StatsService(db, group_ids=group_ids)

    rows = service.ranking_rows("total")

    assert rows[0]["group_labels"] == "第一海岸群"


def test_group_message_totals_are_isolated_and_include_zero_count_groups(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002, 1003))
    db.set_group_info(1001, "group one")
    db.set_group_info(1002, "group two")
    stamp = datetime(2026, 7, 20, 10, 0)
    assert db.record_message("one", 1001, 7, "A", stamp)
    assert db.record_message("two", 1002, 7, "A", stamp)
    assert db.record_message("three", 1002, 8, "B", stamp)

    totals = db.group_message_totals((1002, 1003, 1001), date(2026, 7, 20))

    assert totals == [
        {"group_id": 1002, "group_name": "group two", "message_count": 2},
        {"group_id": 1003, "group_name": "1003", "message_count": 0},
        {"group_id": 1001, "group_name": "group one", "message_count": 1},
    ]


def test_group_daily_message_totals_include_each_day_in_the_requested_range(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    assert db.record_message("first", 1001, 7, "A", datetime(2026, 7, 20, 10, 0))
    assert db.record_message("second", 1001, 7, "A", datetime(2026, 7, 22, 10, 0))

    assert db.group_daily_message_totals(1001, date(2026, 7, 20), date(2026, 7, 22)) == [
        {"day": "2026-07-20", "message_count": 1},
        {"day": "2026-07-21", "message_count": 0},
        {"day": "2026-07-22", "message_count": 1},
    ]


def test_recent_group_daily_totals_uses_a_fixed_seven_day_window(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    db.record_message("late", 1001, 7, "A", datetime(2026, 7, 31, 10, 0))
    service = StatsService(db, group_ids=(1001,))

    rows = service.recent_group_daily_totals(1001, today=date(2026, 7, 31))

    assert [row["day"] for row in rows] == [
        "2026-07-25", "2026-07-26", "2026-07-27", "2026-07-28",
        "2026-07-29", "2026-07-30", "2026-07-31",
    ]
    assert [row["message_count"] for row in rows] == [0, 0, 0, 0, 0, 0, 1]


def test_stats_listener_runs_before_blocking_passive_listeners():
    source = Path("bot/plugins/stats.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    listener = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "message_listener" for target in node.targets)
    )
    assert isinstance(listener, ast.Call)
    values = {keyword.arg: keyword.value for keyword in listener.keywords}
    priority = values["priority"]
    assert isinstance(priority, ast.UnaryOp)
    assert isinstance(priority.op, ast.USub)
    assert isinstance(priority.operand, ast.Constant)
    assert priority.operand.value == 100
    assert isinstance(values["block"], ast.Constant)
    assert values["block"].value is False


def test_restrict_message_statistics_removes_legacy_non_a_coast_aggregates(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    stamp = datetime(2026, 7, 20, 10, 0)
    assert db.record_message("1001:one", 1001, 7, "A", stamp)
    assert db.record_message("1002:one", 1002, 8, "B", stamp)

    db.restrict_message_statistics((1001,))

    assert db.message_ranking((1001, 1002), date(2026, 7, 20)) == [
        {
            "user_id": 7,
            "nickname": "A",
            "message_count": 1,
            "rank": 1,
            "group_labels": "未命名群",
        }
    ]
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_dedup").fetchone()[0] == 1
