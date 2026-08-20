"""Inspect or clear all 今日老婆 relationship history without touching activity or mini-games."""

from __future__ import annotations

import argparse
import json

from bot.config import settings
from bot.db import Database


TARGET_TABLE = "today_wife_records"
PROTECTED_TABLES = (
    "today_wife_activity_counts",
    "today_wife_activity_events",
)


def _count(connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _mini_game_tables(connection) -> tuple[str, ...]:
    rows = connection.execute(
        """SELECT name FROM sqlite_master
           WHERE type='table' AND name LIKE 'mini_game_%'
           ORDER BY name"""
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear all today-wife relationship records while preserving activity and mini-game data."
    )
    parser.add_argument(
        "--confirm-all-groups",
        action="store_true",
        help="Perform the irreversible delete. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    database = Database(settings.db_path)
    with database.connect() as connection:
        protected = PROTECTED_TABLES + _mini_game_tables(connection)
        before = {TARGET_TABLE: _count(connection, TARGET_TABLE)}
        before.update({table: _count(connection, table) for table in protected})
        if not args.confirm_all_groups:
            print(json.dumps({"database": str(settings.db_path), "mode": "inspect", "counts": before}, ensure_ascii=False))
            return 0

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(f'DELETE FROM "{TARGET_TABLE}"')
        after = {TARGET_TABLE: _count(connection, TARGET_TABLE)}
        after.update({table: _count(connection, table) for table in protected})
        changed_protected = {
            table: {"before": before[table], "after": after[table]}
            for table in protected
            if before[table] != after[table]
        }
        if after[TARGET_TABLE] != 0 or changed_protected:
            connection.rollback()
            raise RuntimeError(
                f"reset verification failed: target={after[TARGET_TABLE]}, protected={changed_protected}"
            )
        connection.commit()

    print(
        json.dumps(
            {
                "database": str(settings.db_path),
                "mode": "cleared",
                "deleted": before[TARGET_TABLE],
                "before": before,
                "after": after,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
