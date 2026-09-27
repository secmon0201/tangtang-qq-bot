"""Same-user continuation limits and hooks, independent of QQ event adapters."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ContinuationConfig:
    idle_seconds: float = 30
    hard_seconds: float = 300
    max_attempts: int = 4
    max_silences: int = 2
    debounce_seconds: float = 1.5
    max_debounce_seconds: float = 3
    max_pending: int = 32
    max_messages: int = 8
    max_chars: int = 8000


@dataclass
class ConversationWindow:
    context: object
    opened_at: float
    delivered_at: float
    attempts: int = 0
    silences: int = 0

    def current(self, now: float, config: ContinuationConfig) -> bool:
        return (now - self.delivered_at < config.idle_seconds
                and now - self.opened_at < config.hard_seconds
                and self.silences < config.max_silences)


class ContinuationStore:
    """Attempt caps survive restarts and persona changes; windows do not."""

    def __init__(self, path: Path, timezone: str = "Asia/Shanghai") -> None:
        self.path, self.timezone = path, ZoneInfo(timezone)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS continuation_attempts(
                    request_id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
                    day TEXT NOT NULL, created_at REAL NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'admitted');
                CREATE INDEX IF NOT EXISTS continuation_day
                    ON continuation_attempts(day, group_id);
                CREATE TABLE IF NOT EXISTS continuation_quota_refreshes(
                    day TEXT PRIMARY KEY, started_at REAL NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'started');
                CREATE TABLE IF NOT EXISTS continuation_group_quotas(
                    group_id INTEGER NOT NULL, day TEXT NOT NULL,
                    member_count INTEGER NOT NULL, daily_limit INTEGER NOT NULL,
                    PRIMARY KEY(group_id, day));
            """)

    def day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.timezone).date().isoformat()

    def begin_quota_refresh(self, now: float) -> bool:
        """Reserve once per local day, including failures and process restarts."""
        with sqlite3.connect(self.path, timeout=1) as conn:
            return conn.execute(
                "INSERT OR IGNORE INTO continuation_quota_refreshes(day,started_at) VALUES(?,?)",
                (self.day(now), now)).rowcount == 1

    def finish_quota_refresh(self, now: float, counts: dict[int, int] | None) -> None:
        day = self.day(now)
        with sqlite3.connect(self.path, timeout=1) as conn:
            if counts is not None:
                conn.executemany(
                    "INSERT OR IGNORE INTO continuation_group_quotas VALUES(?,?,?,?)",
                    ((group, day, count, continuation_daily_limit(count))
                     for group, count in counts.items()))
            conn.execute("UPDATE continuation_quota_refreshes SET outcome=? WHERE day=?",
                         ("failed" if counts is None else "completed", day))

    @staticmethod
    def _daily_limit(conn: sqlite3.Connection, group_id: int, day: str) -> int:
        row = conn.execute(
            "SELECT daily_limit FROM continuation_group_quotas "
            "WHERE group_id=? AND day<=? ORDER BY day DESC LIMIT 1",
            (group_id, day)).fetchone()
        return int(row[0]) if row else 20

    def daily_limit(self, group_id: int, now: float) -> int:
        with sqlite3.connect(self.path, timeout=1) as conn:
            return self._daily_limit(conn, group_id, self.day(now))

    def claim(self, group_id: int, request_id: str, now: float) -> bool:
        day = self.day(now)
        with sqlite3.connect(self.path, timeout=1) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM continuation_attempts WHERE request_id=?",
                            (request_id,)).fetchone():
                return False
            group = conn.execute(
                "SELECT count(*) FROM continuation_attempts WHERE group_id=? AND day=?",
                (group_id, day)).fetchone()[0]
            if group >= self._daily_limit(conn, group_id, day):
                return False
            conn.execute("INSERT INTO continuation_attempts(request_id,group_id,day,created_at) VALUES(?,?,?,?)",
                         (request_id, group_id, day, now))
        return True

    def outcome(self, request_id: str, outcome: str) -> None:
        with sqlite3.connect(self.path, timeout=1) as conn:
            conn.execute("UPDATE continuation_attempts SET outcome=? WHERE request_id=?",
                         (outcome, request_id))


def continuation_daily_limit(member_count: int) -> int:
    """Twenty attempts per started hundred members; unknown/empty gets twenty."""
    return max(1, (member_count + 99) // 100) * 20


@dataclass(frozen=True)
class ContinuationTurn:
    admit: Callable[[], bool]
    current: Callable[[], bool]
    outcome: Callable[[str, str], None]


_turn: ContextVar[ContinuationTurn | None] = ContextVar("continuation_turn", default=None)


def continuation_turn() -> ContinuationTurn | None:
    return _turn.get()


@contextmanager
def continuation_turn_for(turn: ContinuationTurn) -> Iterator[None]:
    token = _turn.set(turn)
    try:
        yield
    finally:
        _turn.reset(token)
