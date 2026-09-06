from __future__ import annotations

import json
import sqlite3

from scripts.audit_database_backups import build_audit


def test_backup_audit_reports_structure_without_exporting_records(tmp_path) -> None:
    database = tmp_path / "snapshot" / "bot.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE messages (message_id INTEGER PRIMARY KEY, content TEXT)")
        connection.execute("INSERT INTO messages(content) VALUES ('private-message-body')")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    (tmp_path / "note.txt").write_text("not a database", encoding="utf-8")
    files_before = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())

    audit = build_audit(tmp_path)
    payload = json.dumps(audit, ensure_ascii=False)

    assert audit["total_files"] == len(files_before)
    assert audit["non_sqlite_files"] == len(files_before) - 1
    assert len(audit["sqlite_files"]) == 1
    assert audit["sqlite_files"][0]["quick_check"] == "ok"
    assert audit["sqlite_files"][0]["tables"] == [{"name": "messages", "rows": 1}]
    assert "private-message-body" not in payload
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == files_before
