"""Persistent fixed strategies, heat, atomic quotas and outcome ledger."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.services.proactive_policy import STRATEGIES, TrafficState, decide, LABELS

SCHEMA = """
CREATE TABLE IF NOT EXISTS policy (group_id INTEGER PRIMARY KEY, strategy TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS traffic (group_id INTEGER PRIMARY KEY, payload TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (request_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, strategy TEXT NOT NULL,
 day TEXT NOT NULL, started_at REAL NOT NULL, heat REAL NOT NULL, context_age REAL NOT NULL,
 status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS attempts_day_group ON attempts(day,group_id);
CREATE TABLE IF NOT EXISTS policy_audit (id INTEGER PRIMARY KEY, group_id INTEGER NOT NULL, strategy TEXT NOT NULL, changed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scheduler_health (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, last_tick REAL NOT NULL);
"""


class ProactiveStore:
    def __init__(self, path: Path, timezone: str = "Asia/Shanghai") -> None:
        self.path, self.zone = path, ZoneInfo(timezone)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=.2)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def _selection(c, group_id: int) -> tuple[str, int, int]:
        default = c.execute("SELECT strategy,revision FROM policy WHERE group_id=0").fetchone()
        row = c.execute("SELECT strategy,revision FROM policy WHERE group_id=?", (group_id,)).fetchone()
        return (row["strategy"] if row else default["strategy"] if default else "legacy",
                row["revision"] if row else 0, default["revision"] if default else 0)

    def selection(self, group_id: int) -> tuple[str, int, int]:
        with self.connection() as c:
            return self._selection(c, group_id)

    def set_policy(self, group_id: int, strategy: str, now: float) -> None:
        if strategy not in STRATEGIES or group_id < 0:
            raise ValueError("invalid proactive strategy")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT strategy FROM policy WHERE group_id=?", (group_id,)).fetchone()
            if row and row[0] == strategy:
                return
            c.execute("INSERT INTO policy VALUES(?,?,1) ON CONFLICT(group_id) DO UPDATE SET strategy=excluded.strategy,revision=revision+1", (group_id, strategy))
            c.execute("INSERT INTO policy_audit(group_id,strategy,changed_at) VALUES(?,?,?)", (group_id, strategy, now))

    @staticmethod
    def _state(c, group_id: int) -> TrafficState:
        row = c.execute("SELECT payload FROM traffic WHERE group_id=?", (group_id,)).fetchone()
        return TrafficState(**json.loads(row[0])) if row else TrafficState()

    @staticmethod
    def _save(c, group_id: int, state: TrafficState, reason: str, now: float) -> None:
        c.execute("INSERT INTO traffic VALUES(?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET payload=excluded.payload,reason=excluded.reason,updated_at=excluded.updated_at",
                  (group_id, json.dumps(asdict(state), separators=(",", ":")), reason, now))

    def observe(self, group_id: int, user_id: int, message_id: str, text: str, now: float) -> bool:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            state = self._state(c, group_id)
            changed = state.observe(user_id, message_id, text, now)
            if changed:
                self._save(c, group_id, state, "observed", now)
            return changed

    def _quota_reason(self, c, group_id: int, strategy: str, now: float, low_groups: tuple[int, ...]) -> str:
        day = datetime.fromtimestamp(now, self.zone).date().isoformat()
        rows = c.execute("SELECT group_id,count(*) n FROM attempts WHERE day=? GROUP BY group_id", (day,)).fetchall()
        counts = {r["group_id"]: r["n"] for r in rows}
        total = sum(counts.values())
        if counts.get(group_id, 0) >= (12 if strategy == "low_traffic_v1" else 60):
            return "group_quota"
        reserve = sum(max(0, 12 - counts.get(g, 0)) for g in low_groups if g != group_id)
        if total >= 400 or (strategy != "low_traffic_v1" and total >= max(0, 400 - reserve)):
            return "global_quota"
        return ""

    def candidate(self, group_id: int, now: float, random_value: float, legacy_last: float,
                  low_groups: tuple[int, ...]):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            selection = self._selection(c, group_id)
            state = self._state(c, group_id)
            if now - state.last_tick < 15:
                return False
            state.last_tick = now
            state.last_attempt = max(state.last_attempt, legacy_last)
            reason = self._quota_reason(c, group_id, selection[0], now, low_groups)
            if not reason:
                reason = decide(state, selection[0], now, datetime.fromtimestamp(now, self.zone).hour, random_value).reason
            self._save(c, group_id, state, reason, now)
            return reason == "candidate"

    def current(self, group_id: int, selection: tuple[str, int, int], now: float) -> bool:
        with self.connection() as c:
            state = self._state(c, group_id)
            return self._selection(c, group_id) == selection and now - state.last_message <= 120

    def admit(self, group_id: int, request_id: str, selection: tuple[str, int, int], now: float,
              low_groups: tuple[int, ...]) -> bool:
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            state = self._state(c, group_id)
            if self._selection(c, group_id) != selection or now - state.last_message > 90:
                return False
            cooldown = 1800 if selection[0] == "low_traffic_v1" else 900
            if state.last_attempt and now - state.last_attempt < cooldown:
                return False
            if self._quota_reason(c, group_id, selection[0], now, low_groups):
                return False
            day = datetime.fromtimestamp(now, self.zone).date().isoformat()
            cursor = c.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?,?,?,?)",
                               (request_id, group_id, selection[0], day, now, state.heat,
                                now - state.last_message, "admitted", ""))
            if not cursor.rowcount:
                return False
            state.last_attempt, state.misses = now, 0
            state.consumed_message = request_id.partition(":")[2]
            self._save(c, group_id, state, "admitted", now)
            return True

    def outcome(self, request_id: str, event: str, detail: str = "") -> None:
        mapping = {"model_started": "model_started", "silent": "silent", "proactive_reply": "delivered",
                   "error": "error", "invalid_reply_structure": "invalid", "voice_uncertain": "uncertain",
                   "voice_cancelled": "cancelled", "voice_duplicate": "duplicate", "cancelled": "cancelled"}
        if event == "send_result" and detail.startswith("unconfirmed"):
            event = "cancelled" if "OutboundCancelled" in detail else "voice_uncertain"
        if event not in mapping:
            return
        with self.connection() as c:
            c.execute("UPDATE attempts SET status=?,detail=? WHERE request_id=? AND status NOT IN ('delivered','uncertain')",
                      (mapping[event], detail[:200], request_id))

    def finish(self, request_id: str) -> None:
        with self.connection() as c:
            c.execute("UPDATE attempts SET status='cancelled' WHERE request_id=? AND status IN ('admitted','model_started')", (request_id,))

    def heartbeat(self, now: float) -> None:
        with self.connection() as c:
            c.execute("INSERT OR REPLACE INTO scheduler_health VALUES(1,?,?)", (os.getpid(), now))

    def recover_interrupted(self) -> None:
        with self.connection() as c:
            c.execute("UPDATE attempts SET status='interrupted' WHERE status IN ('admitted','model_started')")

    def status(self, group_id: int, now: float) -> str:
        with self.connection() as c:
            policy = self._selection(c, group_id)
            state = self._state(c, group_id)
            day = datetime.fromtimestamp(now, self.zone).date().isoformat()
            counts = c.execute("SELECT status,count(*) n FROM attempts WHERE group_id=? AND day=? GROUP BY status", (group_id, day)).fetchall()
            row = c.execute("SELECT reason FROM traffic WHERE group_id=?", (group_id,)).fetchone()
            state.decay(now)
            outcomes = "、".join(f"{r['status']}={r['n']}" for r in counts) or "暂无"
            return f"{group_id}：{LABELS[policy[0]]} ({policy[0]})；R={state.rate:.2f}；最近判定={row[0] if row else '等待新消息'}；今日{outcomes}"
