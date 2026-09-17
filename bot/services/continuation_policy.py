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
    idle_seconds: float = 120
    hard_seconds: float = 600
    max_attempts: int = 4
    max_silences: int = 2
    group_daily: int = 20
    global_daily: int = 80
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
            """)

    def claim(self, group_id: int, request_id: str, now: float,
              config: ContinuationConfig) -> bool:
        day = datetime.fromtimestamp(now, self.timezone).date().isoformat()
        with sqlite3.connect(self.path, timeout=1) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM continuation_attempts WHERE request_id=?",
                            (request_id,)).fetchone():
                return False
            total, group = conn.execute(
                "SELECT count(*),coalesce(sum(group_id=?),0) FROM continuation_attempts WHERE day=?",
                (group_id, day)).fetchone()
            if total >= config.global_daily or group >= config.group_daily:
                return False
            conn.execute("INSERT INTO continuation_attempts(request_id,group_id,day,created_at) VALUES(?,?,?,?)",
                         (request_id, group_id, day, now))
        return True

    def outcome(self, request_id: str, outcome: str) -> None:
        with sqlite3.connect(self.path, timeout=1) as conn:
            conn.execute("UPDATE continuation_attempts SET outcome=? WHERE request_id=?",
                         (outcome, request_id))


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
