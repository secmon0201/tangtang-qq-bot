from __future__ import annotations

import csv
import io
import os
import subprocess
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from nonebot import logger

from bot.db import Database


DiagnosticSnapshotFactory = Callable[[tuple[str, ...], str], dict[str, Any]]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _qq_process_snapshot(active_bot_ids: tuple[str, ...], observed_at: str) -> dict[str, Any]:
    """Collect a small process snapshot without reading NapCat logs or config."""
    snapshot: dict[str, Any] = {
        "observed_at": observed_at,
        "onebot_connection_count": len(active_bot_ids),
        "active_bot_ids": list(active_bot_ids),
        "qq_process_count": 0,
        "qq_process_ids": [],
        "qq_process_probe": "unavailable",
    }
    command = (
        ["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"]
        if os.name == "nt"
        else ["pgrep", "-x", "qq"]
    )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        snapshot["qq_process_probe"] = f"failed:{type(exc).__name__}"
        return snapshot

    if os.name == "nt":
        process_ids: list[int] = []
        for row in csv.reader(io.StringIO(completed.stdout)):
            if len(row) < 2 or row[0].strip().lower() != "qq.exe":
                continue
            raw_pid = row[1].strip().replace(",", "")
            if raw_pid.isdigit():
                process_ids.append(int(raw_pid))
        probe_ok = completed.returncode == 0
    else:
        process_ids = [int(line) for line in completed.stdout.splitlines() if line.isdigit()]
        # pgrep returns 1 when no process matched; that is a successful probe.
        probe_ok = completed.returncode in {0, 1}
    snapshot["qq_process_count"] = len(process_ids)
    snapshot["qq_process_ids"] = process_ids
    snapshot["qq_process_probe"] = "ok" if probe_ok else "nonzero_exit"
    return snapshot


class NapCatMaintenanceMonitor:
    """Track OneBot connection edges and persist a single row per outage."""

    def __init__(
        self,
        database: Database,
        snapshot_factory: DiagnosticSnapshotFactory = _qq_process_snapshot,
    ) -> None:
        self._database = database
        self._snapshot_factory = snapshot_factory
        self._active_bot_ids: set[str] = set()
        self._last_connected_bot_ids: set[str] = set()
        self._last_connected_at: str | None = None
        self._has_connected_in_runtime = False
        self._outage_observed = False
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    def reconcile(self, bot_ids: Iterable[str], trigger: str) -> None:
        """Observe current adapter connections; initial offline startup is not an incident."""
        if self._stopping:
            return
        current = {str(bot_id) for bot_id in bot_ids if str(bot_id)}
        was_connected = bool(self._active_bot_ids)
        is_connected = bool(current)
        self._active_bot_ids = current

        if is_connected:
            self._last_connected_at = _timestamp()
            self._last_connected_bot_ids = set(current)
            self._has_connected_in_runtime = True
            if not was_connected:
                self._recover_if_needed(trigger)
            self._outage_observed = False
            return

        if (was_connected or self._has_connected_in_runtime) and not self._outage_observed:
            self._record_disconnect(trigger)
            self._outage_observed = True

    def _snapshot(self) -> dict[str, Any]:
        active = tuple(sorted(self._active_bot_ids))
        snapshot = self._snapshot_factory(active, _timestamp())
        # The storage contract only permits diagnostics we construct locally.
        return {
            "observed_at": str(snapshot.get("observed_at") or _timestamp())[:64],
            "onebot_connection_count": max(0, int(snapshot.get("onebot_connection_count", len(active)))),
            "active_bot_ids": [str(item)[:64] for item in snapshot.get("active_bot_ids", active)],
            "qq_process_count": max(0, int(snapshot.get("qq_process_count", 0))),
            "qq_process_ids": [int(item) for item in snapshot.get("qq_process_ids", []) if str(item).isdigit()],
            "qq_process_probe": str(snapshot.get("qq_process_probe") or "unavailable")[:80],
        }

    def _record_disconnect(self, trigger: str) -> None:
        snapshot = self._snapshot()
        if snapshot["qq_process_probe"] != "ok":
            diagnosis = "OneBot 已断开；QQ 进程状态无法确认"
        elif snapshot["qq_process_count"] == 0:
            diagnosis = "OneBot 已断开；未发现 QQ 进程"
        else:
            diagnosis = "OneBot 已断开；QQ 进程仍在运行，等待 NapCat/QQ 恢复或人工登录"
        incident = self._database.open_napcat_connection_incident(
            last_connected_at=self._last_connected_at,
            bot_self_id=",".join(sorted(self._last_connected_bot_ids)),
            trigger=trigger,
            diagnosis=diagnosis,
            snapshot=snapshot,
        )
        logger.warning(
            "NapCat OneBot outage recorded: incident_id=%s trigger=%s diagnosis=%s",
            incident["incident_id"],
            trigger,
            diagnosis,
        )

    def _recover_if_needed(self, trigger: str) -> None:
        incident = self._database.recover_open_napcat_connection_incident(
            recovery_snapshot=self._snapshot()
        )
        if incident is not None:
            logger.warning(
                "NapCat OneBot connection restored: incident_id=%s trigger=%s duration_seconds=%s",
                incident["incident_id"],
                trigger,
                incident["duration_seconds"],
            )

    def status(self) -> dict[str, Any]:
        return {
            "connected": bool(self._active_bot_ids),
            "active_bot_ids": tuple(sorted(self._active_bot_ids)),
            "last_connected_at": self._last_connected_at,
            "has_connected_in_runtime": self._has_connected_in_runtime,
            "open_incident": self._database.current_napcat_connection_incident(),
        }
