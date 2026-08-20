from __future__ import annotations

from bot.db import Database
from bot.services.napcat_maintenance import NapCatMaintenanceMonitor


def test_napcat_maintenance_records_one_outage_and_its_recovery(tmp_path):
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

    monitor = NapCatMaintenanceMonitor(database, snapshot)
    monitor.reconcile((), "startup")
    assert database.napcat_connection_incidents() == []

    monitor.reconcile(("10001",), "connect_hook")
    monitor.reconcile((), "disconnect_hook")
    monitor.reconcile((), "scheduler")
    rows = database.napcat_connection_incidents()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert "QQ 进程仍在运行" in rows[0]["diagnosis"]

    monitor.reconcile(("10001",), "connect_hook")
    rows = database.napcat_connection_incidents()
    assert len(rows) == 1
    assert rows[0]["status"] == "recovered"
    assert rows[0]["recovered_at"]
    assert rows[0]["duration_seconds"] is not None


def test_napcat_maintenance_marks_missing_qq_process(tmp_path):
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

    monitor = NapCatMaintenanceMonitor(database, snapshot)
    monitor.reconcile(("10001",), "connect_hook")
    monitor.reconcile((), "disconnect_hook")
    row = database.current_napcat_connection_incident()
    assert row is not None
    assert "未发现 QQ 进程" in row["diagnosis"]
