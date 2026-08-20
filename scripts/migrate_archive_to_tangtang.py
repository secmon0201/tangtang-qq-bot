from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
BOT_DB = ROOT / "data" / "bot.db"
TANGTANG_DB = ROOT / "data" / "tangtang" / "tangtang.db"
TZ = ZoneInfo("Asia/Shanghai")
BOUNDARY = datetime(2026, 8, 8, 0, 0, tzinfo=TZ)


def parse_local(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def main() -> None:
    bot = sqlite3.connect(BOT_DB)
    tt = sqlite3.connect(TANGTANG_DB)
    bot.row_factory = sqlite3.Row
    tt.execute("PRAGMA journal_mode=WAL")
    tt.execute("PRAGMA busy_timeout=10000")

    columns = {row[1] for row in tt.execute("PRAGMA table_info(tangtang_group_messages)")}
    if "consumed" not in columns:
        tt.execute(
            "ALTER TABLE tangtang_group_messages "
            "ADD COLUMN consumed INTEGER NOT NULL DEFAULT 0"
        )

    consumed = {
        str(row["event_id"])
        for row in bot.execute("SELECT event_id FROM a_coast_profile_consumed_messages")
    }
    nicknames = {
        int(row["user_id"]): str(row["nickname"] or "")
        for row in bot.execute("SELECT user_id, nickname FROM a_coast_archive_users")
    }

    cursor = bot.execute(
        """
        SELECT m.group_id, m.user_id, m.occurred_at, m.event_id,
               COALESCE(t.content, m.inline_text) AS content
        FROM a_coast_archive_messages AS m
        LEFT JOIN a_coast_archive_texts AS t ON t.text_id = m.text_id
        ORDER BY m.occurred_at ASC, m.event_id ASC
        """
    )
    insert_sql = (
        "INSERT OR IGNORE INTO tangtang_group_messages "
        "(group_id, user_id, nickname, text, message_id, created_at, consumed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    batch: list[tuple] = []
    imported = 0
    skipped_today = 0
    empty = 0
    while True:
        chunk = cursor.fetchmany(5000)
        if not chunk:
            break
        for row in chunk:
            content = str(row["content"] or "")
            if not content:
                empty += 1
                continue
            occurred_at = str(row["occurred_at"] or "")
            if occurred_at and parse_local(occurred_at) >= BOUNDARY:
                skipped_today += 1
                continue
            event_id = str(row["event_id"] or "")
            message_id = event_id.split(":", 1)[1] if ":" in event_id else event_id
            batch.append(
                (
                    int(row["group_id"]),
                    int(row["user_id"]),
                    nicknames.get(int(row["user_id"]), str(row["user_id"])),
                    content,
                    message_id or event_id,
                    occurred_at,
                    1 if event_id in consumed else 0,
                )
            )
            if len(batch) >= 5000:
                imported += tt.executemany(insert_sql, batch).rowcount
                batch = []
    if batch:
        imported += tt.executemany(insert_sql, batch).rowcount
    tt.commit()

    archive_before = bot.execute(
        "SELECT COUNT(*) FROM a_coast_archive_messages"
    ).fetchone()[0]
    profile_states = bot.execute(
        "SELECT COUNT(*) FROM a_coast_profile_states"
    ).fetchone()[0]
    tangtang_total = tt.execute(
        "SELECT COUNT(*) FROM tangtang_group_messages"
    ).fetchone()[0]
    consumed_flagged = tt.execute(
        "SELECT COUNT(*) FROM tangtang_group_messages WHERE consumed = 1"
    ).fetchone()[0]

    for table in (
        "a_coast_archive_messages",
        "a_coast_archive_texts",
        "a_coast_archive_users",
        "a_coast_profile_consumed_messages",
        "ai_memory_messages",
        "ai_memory_people",
        "ai_memory_topics",
        "ai_memory_knowledge",
        "ai_memory_relations",
    ):
        bot.execute(f"DROP TABLE IF EXISTS {table}")
    bot.commit()

    print(f"archive rows before: {archive_before}")
    print(f"imported into tangtang: {imported}")
    print(f"skipped (today): {skipped_today}")
    print(f"skipped (empty text): {empty}")
    print(f"profile states preserved: {profile_states}")
    print(f"tangtang_group_messages total: {tangtang_total}")
    print(f"consumed flagged: {consumed_flagged}")
    print("archive and old AI memory tables dropped")
    bot.close()
    tt.close()


if __name__ == "__main__":
    main()
