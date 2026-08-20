from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import ROOT, settings

DEFAULT_DB_PATH = ROOT / "data" / "tangtang" / "tangtang.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tangtang_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    call_text TEXT NOT NULL,
    reply_text TEXT NOT NULL,
    reply_kind TEXT NOT NULL CHECK (reply_kind IN ('model', 'canned', 'feature', 'proactive')),
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tangtang_calls_user_group
    ON tangtang_calls (user_id, group_id, id);
CREATE INDEX IF NOT EXISTS idx_tangtang_calls_group_recent
    ON tangtang_calls (group_id, id DESC);
CREATE TABLE IF NOT EXISTS tangtang_group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    message_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tangtang_group_messages_dedup
    ON tangtang_group_messages (group_id, message_id);
CREATE INDEX IF NOT EXISTS idx_tangtang_group_messages_group
    ON tangtang_group_messages (group_id, id);
CREATE INDEX IF NOT EXISTS idx_tangtang_group_messages_user
    ON tangtang_group_messages (user_id, group_id, id);
CREATE TABLE IF NOT EXISTS tangtang_proactive_state (
    group_id INTEGER PRIMARY KEY,
    last_reply_at REAL NOT NULL DEFAULT 0,
    messages_since_reply INTEGER NOT NULL DEFAULT 0
);
"""


class TangtangDb:
    """Independent SQLite store for Tangtang interaction history.

    This is a dedicated database file owned by the Tangtang chat module; it
    never touches the existing bot database or its tables.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        self._migrate_schema(conn)
        return conn

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tangtang_group_messages)")
        }
        if "consumed" not in columns:
            conn.execute(
                "ALTER TABLE tangtang_group_messages "
                "ADD COLUMN consumed INTEGER NOT NULL DEFAULT 0"
            )
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tangtang_calls'"
        ).fetchone()
        table_sql = str(row["sql"] or "") if row is not None else ""
        if "'feature'" in table_sql and "'proactive'" in table_sql:
            return
        conn.execute("DROP INDEX IF EXISTS idx_tangtang_calls_user_group")
        conn.execute("ALTER TABLE tangtang_calls RENAME TO tangtang_calls_old")
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO tangtang_calls "
            "(id, group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, created_at) "
            "SELECT id, group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, created_at "
            "FROM tangtang_calls_old"
        )
        conn.execute("DROP TABLE tangtang_calls_old")

    def insert_call(
        self,
        *,
        group_id: int,
        user_id: int,
        message_id: str | int,
        call_text: str,
        reply_text: str,
        reply_kind: str,
        mode: str,
        created_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO tangtang_calls "
                "(group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(group_id),
                    int(user_id),
                    str(message_id or ""),
                    call_text,
                    reply_text,
                    reply_kind,
                    mode,
                    created_at,
                ),
            )

    def claim_proactive_reply(
        self,
        group_id: int,
        now: float,
        probability: float,
        cooldown_seconds: int,
        message_interval: int,
        random_value: float,
    ) -> str:
        """Count one ordinary group message and reserve a proactive slot when allowed."""

        group_id = int(group_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tangtang_proactive_state(group_id) VALUES (?)",
                (group_id,),
            )
            row = conn.execute(
                "SELECT last_reply_at,messages_since_reply "
                "FROM tangtang_proactive_state WHERE group_id=?",
                (group_id,),
            ).fetchone()
            message_count = int(row["messages_since_reply"]) + 1
            conn.execute(
                "UPDATE tangtang_proactive_state SET messages_since_reply=? WHERE group_id=?",
                (message_count, group_id),
            )
            if float(now) < float(row["last_reply_at"]) + int(cooldown_seconds):
                return "cooldown"
            if message_count < int(message_interval):
                return "message_interval"
            if float(random_value) >= float(probability):
                return "probability"
            conn.execute(
                """UPDATE tangtang_proactive_state
                   SET last_reply_at=?,messages_since_reply=0 WHERE group_id=?""",
                (float(now), group_id),
            )
            return "claimed"

    def insert_group_message(
        self,
        *,
        group_id: int,
        user_id: int,
        nickname: str,
        text: str,
        message_id: str | int,
        created_at: str,
    ) -> None:
        """Persist one group message so chat history survives restarts."""

        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tangtang_group_messages "
                "(group_id, user_id, nickname, text, message_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    int(group_id),
                    int(user_id),
                    str(nickname or ""),
                    str(text or ""),
                    str(message_id or ""),
                    created_at,
                ),
            )

    def recent_group_messages(
        self,
        group_id: int,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Most recent group messages, oldest first (prompt-friendly)."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tangtang_group_messages WHERE group_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (int(group_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def recent_user_messages(
        self,
        user_id: int,
        group_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """A single user's recent messages in one group, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tangtang_group_messages "
                "WHERE user_id = ? AND group_id = ? ORDER BY id DESC LIMIT ?",
                (int(user_id), int(group_id), int(limit)),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def profile_messages(
        self,
        user_id: int,
        group_ids: tuple[int, ...],
        keyword: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Profile records for one user across the given groups (oldest first)."""

        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        clauses = ["user_id = ?", f"group_id IN ({placeholders})"]
        parameters: list[Any] = [int(user_id), *selected]
        if keyword:
            clauses.append("instr(text, ?) > 0")
            parameters.append(str(keyword))
        parameters.extend((max(1, min(int(limit), 100)), max(0, int(offset))))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tangtang_group_messages "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def profile_message_count(
        self,
        user_id: int,
        group_ids: tuple[int, ...],
        keyword: str = "",
    ) -> int:
        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            return 0
        placeholders = ",".join("?" for _ in selected)
        clauses = ["user_id = ?", f"group_id IN ({placeholders})"]
        parameters: list[Any] = [int(user_id), *selected]
        if keyword:
            clauses.append("instr(text, ?) > 0")
            parameters.append(str(keyword))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM tangtang_group_messages "
                f"WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
        return int(row[0])

    def profile_summary(
        self,
        user_id: int,
        group_ids: tuple[int, ...],
    ) -> dict[str, Any]:
        """Local profile stats: total, group split, hour split and repeats."""

        rows = self.profile_messages(user_id, group_ids, limit=100000)
        groups: dict[int, int] = {}
        hours: dict[int, int] = {}
        repeats: dict[str, int] = {}
        for row in rows:
            group_id = int(row["group_id"])
            groups[group_id] = groups.get(group_id, 0) + 1
            hour = _local_hour(str(row.get("created_at") or ""))
            if hour is not None:
                hours[hour] = hours.get(hour, 0) + 1
            text = str(row.get("text") or "")
            if text:
                repeats[text] = repeats.get(text, 0) + 1
        return {
            "message_count": len(rows),
            "groups": [
                {"group_id": group_id, "message_count": count}
                for group_id, count in sorted(
                    groups.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "hours": [
                {"hour": hour, "message_count": count}
                for hour, count in sorted(
                    hours.items(), key=lambda item: (-item[1], item[0])
                )
            ],
            "repeats": [
                {"content": text, "message_count": count}
                for text, count in sorted(
                    (
                        (text, count)
                        for text, count in repeats.items()
                        if count > 1
                    ),
                    key=lambda item: (-item[1], -len(item[0])),
                )[:20]
            ],
        }

    def unconsumed_profile_messages(
        self,
        user_id: int,
        group_ids: tuple[int, ...],
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        selected = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        if not selected:
            return []
        placeholders = ",".join("?" for _ in selected)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tangtang_group_messages "
                f"WHERE user_id = ? AND group_id IN ({placeholders}) AND consumed = 0 "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (int(user_id), *selected, max(1, min(int(limit), 100000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def consume_profile_messages(
        self,
        user_id: int,
        row_ids: tuple[int, ...],
    ) -> int:
        values = tuple(dict.fromkeys(int(row_id) for row_id in row_ids if row_id))
        if not values:
            return 0
        placeholders = ",".join("?" for _ in values)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE tangtang_group_messages SET consumed = 1 "
                f"WHERE user_id = ? AND id IN ({placeholders}) AND consumed = 0",
                (int(user_id), *values),
            )
            return cursor.rowcount

    def list_calls(
        self,
        user_id: int,
        group_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if group_id is None:
                rows = conn.execute(
                    "SELECT * FROM tangtang_calls WHERE user_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (int(user_id), int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tangtang_calls WHERE user_id = ? AND group_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (int(user_id), int(group_id), int(limit)),
                ).fetchall()
        return [dict(row) for row in rows]

    def has_recent_group_reply_text(
        self, group_id: int, text: str, limit: int = 10
    ) -> bool:
        """Return whether text matches one of the group's recent Tangtang replies."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT reply_text FROM tangtang_calls "
                "WHERE group_id = ? ORDER BY id DESC LIMIT ?",
                (int(group_id), int(limit)),
            ).fetchall()
        return any(str(row["reply_text"]) == str(text) for row in rows)

    def model_reply_lines(
        self,
        user_id: int,
        group_id: int,
        limit: int,
        max_chars: int,
    ) -> str:
        """Return recent model-generated exchanges formatted for prompt context."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT call_text, reply_text FROM tangtang_calls "
                "WHERE user_id = ? AND group_id = ? "
                "AND reply_kind IN ('model', 'proactive') "
                "ORDER BY id DESC LIMIT ?",
                (int(user_id), int(group_id), int(limit)),
            ).fetchall()
        lines = [
            f"群友说：{row['call_text']}\n糖糖说：{row['reply_text']}"
            for row in reversed(rows)
        ]
        if not lines:
            return ""
        text = "\n\n".join(lines)
        if len(text) <= max_chars:
            return text
        return "…" + text[-max_chars:]


def _local_hour(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(settings.timezone))
    return dt.hour
