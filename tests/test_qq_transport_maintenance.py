from __future__ import annotations

import sqlite3

from bot.db import Database
from bot.services.qq_transport_maintenance import QQTransportMaintenanceMonitor


def test_qq_transport_maintenance_records_one_outage_and_its_recovery(tmp_path):
    database = Database(tmp_path / "bot.db")

    def snapshot(bot_ids: tuple[str, ...], observed_at: str):
        return {
            "observed_at": observed_at,
            "onebot_connection_count": len(bot_ids),
            "active_bot_ids": list(bot_ids),
            "qq_process_count": 1,
            "qq_process_ids": [1234],
            "qq_process_probe": "ok",
        }

    monitor = QQTransportMaintenanceMonitor(database, snapshot)
    monitor.reconcile((), "startup")
    assert database.qq_transport_connection_incidents() == []

    monitor.reconcile(("10001",), "connect_hook")
    monitor.reconcile((), "disconnect_hook")
    monitor.reconcile((), "scheduler")
    rows = database.qq_transport_connection_incidents()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert "QQ 进程仍在运行" in rows[0]["diagnosis"]

    monitor.reconcile(("10001",), "connect_hook")
    rows = database.qq_transport_connection_incidents()
    assert len(rows) == 1
    assert rows[0]["status"] == "recovered"
    assert rows[0]["recovered_at"]
    assert rows[0]["duration_seconds"] is not None


def test_qq_transport_maintenance_marks_missing_qq_process(tmp_path):
    database = Database(tmp_path / "bot.db")

    def snapshot(bot_ids: tuple[str, ...], observed_at: str):
        return {
            "observed_at": observed_at,
            "onebot_connection_count": len(bot_ids),
            "active_bot_ids": list(bot_ids),
            "qq_process_count": 0,
            "qq_process_ids": [],
            "qq_process_probe": "ok",
        }

    monitor = QQTransportMaintenanceMonitor(database, snapshot)
    monitor.reconcile(("10001",), "connect_hook")
    monitor.reconcile((), "disconnect_hook")
    row = database.current_qq_transport_connection_incident()
    assert row is not None
    assert "未发现 QQ 进程" in row["diagnosis"]


def test_legacy_transport_incidents_are_migrated_without_data_loss(tmp_path):
    path = tmp_path / "bot.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE napcat_connection_incidents (
                incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT NOT NULL,
                last_connected_at TEXT,
                recovered_at TEXT,
                duration_seconds INTEGER,
                bot_self_id TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                diagnosis TEXT NOT NULL DEFAULT '',
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                recovery_snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO napcat_connection_incidents
                (detected_at,trigger,status,diagnosis)
                VALUES ('2026-01-01T00:00:00+00:00','legacy','recovered','preserved');
            """
        )

    database = Database(path)
    rows = database.qq_transport_connection_incidents()
    assert len(rows) == 1
    assert rows[0]["diagnosis"] == "preserved"
    with database.connect() as connection:
        legacy = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='napcat_connection_incidents'"
        ).fetchone()
    assert legacy is None
