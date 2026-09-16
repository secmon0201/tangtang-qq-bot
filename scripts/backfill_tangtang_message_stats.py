"""Backfill confirmed Tangtang group sends into the message statistics."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.config import ROOT, settings
from bot.db import Database


DEFAULT_SOURCE_DB = ROOT / "data" / "tangtang" / "tangtang.db"
DEFAULT_NICKNAME = "糖糖"


@dataclass(frozen=True, slots=True)
class ConfirmedReply:
    group_id: int
    platform_message_id: str
    sent_at: datetime

    @property
    def event_id(self) -> str:
        return f"{self.group_id}:{self.platform_message_id}"


def read_confirmed_replies(
    source_db: Path,
    start_day: date,
    end_day: date,
    zone: ZoneInfo,
) -> list[ConfirmedReply]:
    """Read only Tangtang parts confirmed by a non-empty platform message ID."""

    source_uri = f"file:{source_db.resolve()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as connection:
        rows = connection.execute(
            """SELECT c.group_id, p.platform_message_id, p.created_at
               FROM tangtang_reply_parts AS p
               JOIN tangtang_calls AS c ON c.id = p.call_id
               WHERE p.delivered = 1
                 AND p.platform_message_id <> ''
                 AND substr(p.created_at, 1, 10) >= ?
                 AND substr(p.created_at, 1, 10) <= ?
               ORDER BY p.created_at, c.group_id, p.id""",
            (start_day.isoformat(), end_day.isoformat()),
        ).fetchall()

    replies: list[ConfirmedReply] = []
    seen: set[str] = set()
    for group_id, platform_message_id, created_at in rows:
        try:
            sent_at = datetime.fromisoformat(str(created_at))
        except ValueError:
            continue
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=zone)
        sent_at = sent_at.astimezone(zone)
        reply = ConfirmedReply(int(group_id), str(platform_message_id), sent_at)
        if reply.event_id not in seen:
            replies.append(reply)
            seen.add(reply.event_id)
    return replies


def _existing_event_ids(database: Database, event_ids: list[str]) -> set[str]:
    if not event_ids:
        return set()
    with database.connect() as connection:
        placeholders = ",".join("?" for _ in event_ids)
        rows = connection.execute(
            f"SELECT event_id FROM event_dedup WHERE event_id IN ({placeholders})",
            event_ids,
        ).fetchall()
    return {str(row[0]) for row in rows}


def build_report(
    replies: list[ConfirmedReply],
    database: Database,
    bot_id: int,
    nickname: str,
) -> dict[str, object]:
    managed = [reply for reply in replies if database.is_managed_group(reply.group_id)]
    unmanaged = len(replies) - len(managed)
    existing = _existing_event_ids(database, [reply.event_id for reply in managed])
    pending = [reply for reply in managed if reply.event_id not in existing]
    counts = Counter((reply.sent_at.date().isoformat(), reply.group_id) for reply in pending)
    return {
        "bot_id": bot_id,
        "nickname": nickname,
        "source_rows": len(replies),
        "managed_rows": len(managed),
        "pending_rows": len(pending),
        "already_counted": len(existing),
        "unmanaged_rows": unmanaged,
        "by_day_group": [
            {"day": day, "group_id": group_id, "count": count}
            for (day, group_id), count in sorted(counts.items())
        ],
    }


def apply_replies(
    replies: list[ConfirmedReply],
    database: Database,
    bot_id: int,
    nickname: str,
) -> dict[str, int]:
    result = {"imported": 0, "duplicates": 0, "unmanaged": 0}
    for reply in replies:
        if not database.is_managed_group(reply.group_id):
            result["unmanaged"] += 1
            continue
        if database.record_message(
            reply.event_id,
            reply.group_id,
            bot_id,
            nickname,
            reply.sent_at,
        ):
            result["imported"] += 1
        else:
            result["duplicates"] += 1
    return result


def _default_bot_id() -> int:
    raw = os.getenv("QQ_ACCOUNT_ID", "").strip()
    if not raw.isdigit():
        raise SystemExit("QQ_ACCOUNT_ID is required, or pass --bot-id explicitly.")
    return int(raw)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill confirmed Tangtang group sends into message statistics."
    )
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--start-day", type=date.fromisoformat, required=True)
    parser.add_argument("--end-day", type=date.fromisoformat)
    parser.add_argument("--bot-id", type=int, default=_default_bot_id())
    parser.add_argument("--nickname", default=DEFAULT_NICKNAME)
    parser.add_argument("--apply", action="store_true", help="write pending rows to bot.db")
    args = parser.parse_args()
    end_day = args.end_day or args.start_day
    if end_day < args.start_day:
        raise SystemExit("--end-day must not be earlier than --start-day.")
    source_db = args.source_db.resolve()
    if not source_db.is_file():
        raise SystemExit(f"Tangtang database does not exist: {source_db}")

    database = Database(settings.db_path)
    replies = read_confirmed_replies(
        source_db,
        args.start_day,
        end_day,
        ZoneInfo(settings.timezone),
    )
    report = build_report(replies, database, args.bot_id, args.nickname)
    if args.apply:
        report["applied"] = apply_replies(replies, database, args.bot_id, args.nickname)
    else:
        report["applied"] = None
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
