"""Audit and correction ledger for skill invocation and upstream drift."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.config import ROOT


DEFAULT_DB_PATH = ROOT / "data" / "skills" / "audit.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_audit_entries(
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    group_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    skill_id TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    resolved_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_skill_audit_status
    ON skill_audit_entries(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_audit_skill
    ON skill_audit_entries(skill_id, created_at DESC);
"""

CATEGORIES = frozenset(
    {
        "parameter_error",
        "capability_missing",
        "upstream_drift",
        "model_expression",
        "data_stale",
        "contract_violation",
        "unknown",
    }
)
SEVERITIES = frozenset({"info", "warning", "error"})
AUTO_CATEGORIES = frozenset({"parameter_error", "capability_missing"})


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: str
    created_at: float
    updated_at: float
    group_id: int
    user_id: int
    skill_id: str
    category: str
    severity: str
    status: str
    source: str
    detail: str
    resolution: str
    resolved_at: float


def classify_failure(error: str, *, source: str = "") -> tuple[str, str]:
    """Map a raw failure signal to a category and severity."""

    text = str(error or "").lower()
    if "missing git metadata" in text or "commit mismatch" in text:
        return "upstream_drift", "error"
    if "unsupported" in text and "skill" in text:
        return "capability_missing", "error"
    if any(token in text for token in ("schema", "contract", "json", "missing field")):
        return "contract_violation", "error"
    if any(token in text for token in ("invalid", "outofrange", "requires", "parameter")):
        return "parameter_error", "warning"
    if any(token in text for token in ("timeout", "stale", "expired")):
        return "data_stale", "warning"
    if "model" in text or "router" in text:
        return "model_expression", "warning"
    if "capability" in text or "unsupported" in text:
        return "capability_missing", "error"
    return "unknown", "error"


class SkillAuditLedger:
    def __init__(self, path: Path | None = None, *, now: Any = None) -> None:
        self.path = path or DEFAULT_DB_PATH
        self._now = now or time.time

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(SCHEMA)
        return conn

    def record(
        self,
        *,
        skill_id: str,
        category: str,
        severity: str,
        group_id: int = 0,
        user_id: int = 0,
        source: str = "",
        detail: str = "",
    ) -> AuditEntry:
        if category not in CATEGORIES:
            raise ValueError(f"unsupported audit category: {category}")
        if severity not in SEVERITIES:
            raise ValueError(f"unsupported audit severity: {severity}")
        now = float(self._now())
        entry_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO skill_audit_entries "
                "(id, created_at, updated_at, group_id, user_id, skill_id, category, "
                "severity, status, source, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (
                    entry_id,
                    now,
                    now,
                    int(group_id),
                    int(user_id),
                    str(skill_id),
                    category,
                    severity,
                    str(source)[:200],
                    str(detail)[:1000],
                ),
            )
        return self.get(entry_id)  # type: ignore[return-value]

    def record_failure(
        self,
        *,
        skill_id: str,
        error: str,
        group_id: int = 0,
        user_id: int = 0,
        source: str = "skill_invocation",
    ) -> AuditEntry:
        category, severity = classify_failure(error, source=source)
        return self.record(
            skill_id=skill_id,
            category=category,
            severity=severity,
            group_id=group_id,
            user_id=user_id,
            source=source,
            detail=error,
        )

    def get(self, entry_id: str) -> AuditEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM skill_audit_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return _to_entry(row) if row else None

    def list_entries(
        self,
        *,
        status: str = "",
        skill_id: str = "",
        category: str = "",
        limit: int = 50,
    ) -> list[AuditEntry]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        if skill_id:
            clauses.append("skill_id = ?")
            parameters.append(skill_id)
        if category:
            clauses.append("category = ?")
            parameters.append(category)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM skill_audit_entries {where} ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [_to_entry(row) for row in rows]

    def resolve(self, entry_id: str, resolution: str = "resolved") -> bool:
        now = float(self._now())
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE skill_audit_entries SET status = 'resolved', resolution = ?, "
                "updated_at = ?, resolved_at = ? WHERE id = ? AND status != 'resolved'",
                (str(resolution)[:500], now, now, entry_id),
            )
            return cursor.rowcount > 0

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            by_status = dict(
                conn.execute(
                    "SELECT status, count(*) FROM skill_audit_entries GROUP BY status"
                )
            )
            by_category = dict(
                conn.execute(
                    "SELECT category, count(*) FROM skill_audit_entries "
                    "WHERE status != 'resolved' GROUP BY category"
                )
            )
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM skill_audit_entries WHERE status != 'resolved'"
            ).fetchone()[0]
        now = float(self._now())
        return {
            "by_status": by_status,
            "open_by_category": by_category,
            "oldest_open_seconds": max(0.0, now - float(oldest)) if oldest else 0.0,
        }

    def export(self, path: Path) -> int:
        entries = self.list_entries(limit=500)
        payload = [
            {
                "id": entry.id,
                "skill_id": entry.skill_id,
                "category": entry.category,
                "severity": entry.severity,
                "status": entry.status,
                "detail": entry.detail,
                "resolution": entry.resolution,
            }
            for entry in entries
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return len(payload)


def _to_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        group_id=int(row["group_id"]),
        user_id=int(row["user_id"]),
        skill_id=str(row["skill_id"]),
        category=str(row["category"]),
        severity=str(row["severity"]),
        status=str(row["status"]),
        source=str(row["source"]),
        detail=str(row["detail"]),
        resolution=str(row["resolution"]),
        resolved_at=float(row["resolved_at"]),
    )


ledger = SkillAuditLedger()
