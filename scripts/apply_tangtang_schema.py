from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from bot.services.tangtang_db import DEFAULT_DB_PATH, TangtangDb


EXPECTED_TABLES = (
    "tangtang_reply_parts",
    "tangtang_self_versions",
    "tangtang_memories",
    "tangtang_relationship_state",
    "tangtang_persona_state",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply and verify the additive Tangtang schema")
    parser.add_argument("--database", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    database = args.database.resolve()
    connection = TangtangDb(database)._connect()
    try:
        with connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            calls = int(connection.execute("SELECT COUNT(*) FROM tangtang_calls").fetchone()[0])
            group_messages = int(
                connection.execute("SELECT COUNT(*) FROM tangtang_group_messages").fetchone()[0]
            )
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    finally:
        try:
            connection.close()
        except sqlite3.ProgrammingError:
            pass
    missing = sorted(set(EXPECTED_TABLES) - tables)
    ok = quick_check == "ok" and not missing
    print(
        json.dumps(
            {
                "ok": ok,
                "database": str(database),
                "quick_check": quick_check,
                "calls": calls,
                "group_messages": group_messages,
                "missing_tables": missing,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
