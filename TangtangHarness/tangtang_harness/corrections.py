"""Local tool correction records, migrated from the legacy skill ledger.

The ledger stores actual failures and operator notes in the new business database.
It has no influence on tool admission or execution.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
from typing import Any
import uuid


CATEGORIES = frozenset({"parameter_error", "capability_missing", "upstream_drift",
    "model_expression", "data_stale", "contract_violation", "unknown"})
SEVERITIES = frozenset({"info", "warning", "error"})
SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_audit_entries(
 id TEXT PRIMARY KEY, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 group_id INTEGER NOT NULL DEFAULT 0, user_id INTEGER NOT NULL DEFAULT 0,
 skill_id TEXT NOT NULL DEFAULT '', category TEXT NOT NULL, severity TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', source TEXT NOT NULL DEFAULT '',
 detail TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT '',
 resolved_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_skill_audit_status
 ON skill_audit_entries(status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_audit_skill
 ON skill_audit_entries(skill_id,created_at DESC);
"""
_CREDENTIAL = re.compile(r"(?i)\b(bearer|token|password|secret|api[_-]?key)\b\s*[:=]?\s*[^\s,;]+")
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b")


def redact(text: str, maximum: int = 1000) -> str:
    value = _CREDENTIAL.sub(lambda match: match.group(1) + "=<redacted>", str(text))
    return _API_KEY.sub("<redacted>", value)[:maximum]


def classify_failure(error: str) -> tuple[str, str]:
    text = error.lower()
    if "missing git metadata" in text or "commit mismatch" in text:
        return "upstream_drift", "error"
    if "unsupported" in text and "skill" in text:
        return "capability_missing", "error"
    if any(value in text for value in ("schema", "contract", "json", "missing field")):
        return "contract_violation", "error"
    if any(value in text for value in ("invalid", "outofrange", "requires", "parameter")):
        return "parameter_error", "warning"
    if any(value in text for value in ("timeout", "stale", "expired")):
        return "data_stale", "warning"
    if "model" in text or "router" in text:
        return "model_expression", "warning"
    if "capability" in text or "unsupported" in text:
        return "capability_missing", "error"
    return "unknown", "error"


class SkillCorrectionLedger:
    def __init__(self, db: Any, *, now: Any = time.time) -> None:
        self.db, self.now = db, now
        with db.connect() as conn:
            conn.executescript(SCHEMA)

    def record(self, *, skill_id: str, category: str, severity: str,
               group_id: int = 0, user_id: int = 0, source: str = "",
               detail: str = "") -> dict[str, Any]:
        if category not in CATEGORIES or severity not in SEVERITIES:
            raise ValueError("纠错类别或严重程度不正确。")
        entry_id, now = uuid.uuid4().hex, float(self.now())
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO skill_audit_entries
                (id,created_at,updated_at,group_id,user_id,skill_id,category,severity,source,detail)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, now, now, group_id, user_id, skill_id, category, severity,
                 source[:200], redact(detail)))
        return self.get(entry_id)

    def record_failure(self, *, skill_id: str, error: str, group_id: int = 0,
                       user_id: int = 0, source: str = "skill_invocation") -> dict[str, Any]:
        category, severity = classify_failure(error)
        return self.record(skill_id=skill_id, category=category, severity=severity,
                           group_id=group_id, user_id=user_id, source=source, detail=error)

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM skill_audit_entries WHERE id=?", (entry_id,)).fetchone()
        return dict(row) if row else None

    def list_entries(self, *, status: str = "", skill_id: str = "", category: str = "",
                     limit: int = 50) -> list[dict[str, Any]]:
        filters = {key: value for key, value in (("status", status), ("skill_id", skill_id),
                   ("category", category)) if value}
        where = " WHERE " + " AND ".join(key + "=?" for key in filters) if filters else ""
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM skill_audit_entries" +
                where + " ORDER BY created_at DESC LIMIT ?", (*filters.values(), limit))]

    def resolve(self, entry_id: str, resolution: str = "resolved") -> bool:
        now = float(self.now())
        with self.db.connect() as conn:
            return bool(conn.execute("""UPDATE skill_audit_entries SET status='resolved',
                resolution=?,updated_at=?,resolved_at=? WHERE id=? AND status!='resolved'""",
                (redact(resolution, 500), now, now, entry_id)).rowcount)

    def summary(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            by_status = dict(conn.execute("SELECT status,COUNT(*) FROM skill_audit_entries GROUP BY status"))
            by_category = dict(conn.execute("SELECT category,COUNT(*) FROM skill_audit_entries WHERE status!='resolved' GROUP BY category"))
            oldest = conn.execute("SELECT MIN(created_at) FROM skill_audit_entries WHERE status!='resolved'").fetchone()[0]
        return {"by_status": by_status, "open_by_category": by_category,
                "oldest_open_seconds": max(0, float(self.now()) - oldest) if oldest is not None else 0}

    def export(self, path: Path) -> int:
        entries = self.list_entries(limit=500)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(entries)

    def purge_user(self, user_id: int) -> int:
        with self.db.connect() as conn:
            return conn.execute("DELETE FROM skill_audit_entries WHERE user_id=?", (user_id,)).rowcount
