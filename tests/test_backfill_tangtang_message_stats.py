import sqlite3
from datetime import date
from zoneinfo import ZoneInfo

from bot.db import Database
from scripts.backfill_tangtang_message_stats import (
    apply_replies,
    build_report,
    read_confirmed_replies,
)


def _source_db(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE tangtang_calls (
            id INTEGER PRIMARY KEY,
            group_id INTEGER NOT NULL
        );
        CREATE TABLE tangtang_reply_parts (
            id INTEGER PRIMARY KEY,
            call_id INTEGER NOT NULL,
            delivered INTEGER NOT NULL,
            platform_message_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO tangtang_calls(id, group_id) VALUES (?, ?)",
        [(1, 1001), (2, 1001), (3, 1002)],
    )
    connection.executemany(
        """INSERT INTO tangtang_reply_parts
           (id, call_id, delivered, platform_message_id, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            (1, 1, 1, "sent-1", "2026-09-08T10:00:00+08:00"),
            (2, 2, 0, "sent-2", "2026-09-08T10:01:00+08:00"),
            (3, 3, 1, "", "2026-09-08T10:02:00+08:00"),
            (4, 1, 1, "sent-3", "2026-09-09T10:03:00+08:00"),
        ],
    )
    connection.commit()
    connection.close()


def test_read_confirmed_replies_filters_delivery_and_day(tmp_path):
    source = tmp_path / "tangtang.db"
    _source_db(source)

    rows = read_confirmed_replies(
        source,
        date(2026, 9, 8),
        date(2026, 9, 8),
        ZoneInfo("Asia/Shanghai"),
    )

    assert [(row.group_id, row.platform_message_id) for row in rows] == [(1001, "sent-1")]


def test_apply_replies_is_idempotent_and_reports_existing_rows(tmp_path):
    source = tmp_path / "tangtang.db"
    target = Database(tmp_path / "bot.db")
    target.configure_groups((1001,))
    _source_db(source)
    rows = read_confirmed_replies(
        source,
        date(2026, 9, 8),
        date(2026, 9, 8),
        ZoneInfo("Asia/Shanghai"),
    )

    assert build_report(rows, target, 999, "糖糖")["pending_rows"] == 1
    assert apply_replies(rows, target, 999, "糖糖") == {
        "imported": 1,
        "duplicates": 0,
        "unmanaged": 0,
    }
    assert apply_replies(rows, target, 999, "糖糖") == {
        "imported": 0,
        "duplicates": 1,
        "unmanaged": 0,
    }
    assert target.today_counts(1001, date(2026, 9, 8))[0]["message_count"] == 1
