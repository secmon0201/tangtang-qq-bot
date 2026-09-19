"""Per-skill usage, latency and cost accounting."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from bot.config import ROOT


DEFAULT_DB_PATH = ROOT / "data" / "skills" / "metrics.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_usage(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    skill_id TEXT NOT NULL,
    group_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 1,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0.0,
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_skill_usage_day
    ON skill_usage(created_at, skill_id);
CREATE INDEX IF NOT EXISTS idx_skill_usage_group
    ON skill_usage(group_id, created_at);
"""


class SkillMetricsStore:
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
        group_id: int = 0,
        user_id: int = 0,
        action: str = "",
        ok: bool = True,
        latency_ms: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost: float = 0.0,
        source: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO skill_usage "
                "(created_at, skill_id, group_id, user_id, action, ok, latency_ms, "
                "prompt_tokens, completion_tokens, reasoning_tokens, cost, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    float(self._now()),
                    str(skill_id),
                    int(group_id),
                    int(user_id),
                    str(action)[:60],
                    1 if ok else 0,
                    max(0, int(latency_ms)),
                    max(0, int(prompt_tokens)),
                    max(0, int(completion_tokens)),
                    max(0, int(reasoning_tokens)),
                    max(0.0, float(cost)),
                    str(source)[:60],
                ),
            )

    def summary(self, *, since: float = 0.0, limit: int = 50) -> dict[str, Any]:
        with self._connect() as conn:
            totals = conn.execute(
                "SELECT count(*) AS calls, sum(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failures, "
                "sum(cost) AS cost, sum(prompt_tokens + completion_tokens + reasoning_tokens) "
                "AS tokens FROM skill_usage WHERE created_at >= ?",
                (float(since),),
            ).fetchone()
            by_skill = [
                dict(row)
                for row in conn.execute(
                    "SELECT skill_id, count(*) AS calls, "
                    "sum(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failures, sum(cost) AS cost, "
                    "avg(latency_ms) AS avg_latency_ms FROM skill_usage "
                    "WHERE created_at >= ? GROUP BY skill_id "
                    "ORDER BY calls DESC LIMIT ?",
                    (float(since), max(1, min(int(limit), 200))),
                )
            ]
            by_group = [
                dict(row)
                for row in conn.execute(
                    "SELECT group_id, count(*) AS calls, "
                    "sum(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failures, sum(cost) AS cost "
                    "FROM skill_usage WHERE created_at >= ? GROUP BY group_id "
                    "ORDER BY calls DESC LIMIT ?",
                    (float(since), max(1, min(int(limit), 200))),
                )
            ]
        calls = int(totals["calls"] or 0)
        failures = int(totals["failures"] or 0)
        return {
            "calls": calls,
            "failures": failures,
            "failure_rate": round(failures / calls, 4) if calls else 0.0,
            "cost": round(float(totals["cost"] or 0.0), 6),
            "tokens": int(totals["tokens"] or 0),
            "by_skill": by_skill,
            "by_group": by_group,
        }

    def purge_user(self, user_id: int) -> dict[str, int]:
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM skill_usage WHERE user_id = ?", (int(user_id),)
            ).rowcount
        return {"skill_usage": deleted}


metrics = SkillMetricsStore()
