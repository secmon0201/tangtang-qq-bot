from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from bot.services.tangtang_db import TangtangDb


LEGACY_TABLES = ("tangtang_calls", "tangtang_group_messages", "tangtang_proactive_state")
NEW_TABLES = (
    "tangtang_reply_parts",
    "tangtang_self_versions",
    "tangtang_memories",
    "tangtang_relationship_state",
    "tangtang_persona_state",
)


def _counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in LEGACY_TABLES
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse Tangtang schema migration on a copy")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    source = args.database.resolve()
    if not source.is_file():
        raise SystemExit(f"Database does not exist: {source}")
    before = _counts(source)
    with tempfile.TemporaryDirectory(prefix="tangtang-migration-") as directory:
        candidate = Path(directory) / "tangtang.db"
        shutil.copy2(source, candidate)
        migration_connection = TangtangDb(candidate)._connect()
        try:
            with migration_connection:
                pass
        finally:
            try:
                migration_connection.close()
            except sqlite3.ProgrammingError:
                pass
        after = _counts(candidate)
        connection = sqlite3.connect(candidate)
        try:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
    missing = sorted(set(NEW_TABLES) - tables)
    ok = before == after and quick_check == "ok" and not missing
    print(
        json.dumps(
            {
                "ok": ok,
                "legacy_counts_before": before,
                "legacy_counts_after": after,
                "quick_check": quick_check,
                "missing_tables": missing,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
