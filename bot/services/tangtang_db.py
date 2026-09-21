from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bot.config import ROOT, settings
from bot.services.persona_mood import mood_decay
from bot.services.persona_inbox import SCHEMA as INBOX_SCHEMA, capture

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
    provenance TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS tangtang_reply_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    part_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    platform_message_id TEXT NOT NULL DEFAULT '',
    error_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(call_id, part_index)
);
CREATE INDEX IF NOT EXISTS idx_tangtang_reply_parts_call
    ON tangtang_reply_parts (call_id, part_index);
CREATE TABLE IF NOT EXISTS tangtang_self_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_hash TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tangtang_self_versions_active
    ON tangtang_self_versions (active, id DESC);
CREATE TABLE IF NOT EXISTS tangtang_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('candidate', 'active', 'archived', 'deleted')),
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    source_message_id TEXT NOT NULL DEFAULT '',
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    deleted_at TEXT,
    UNIQUE(group_id, user_id, kind, source_hash)
);
CREATE INDEX IF NOT EXISTS idx_tangtang_memories_scope
    ON tangtang_memories (group_id, user_id, status, updated_at DESC);
CREATE TABLE IF NOT EXISTS tangtang_relationship_state (
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    familiarity REAL NOT NULL DEFAULT 0,
    warmth REAL NOT NULL DEFAULT 0.5,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE TABLE IF NOT EXISTS tangtang_persona_state (
    group_id INTEGER PRIMARY KEY,
    valence REAL NOT NULL DEFAULT 0.5,
    energy REAL NOT NULL DEFAULT 0.5,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_summary_topics (
    topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '',
    unresolved TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    source_count INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_summary_topics_group
    ON group_summary_topics (group_id, state, updated_at DESC);
CREATE TABLE IF NOT EXISTS group_summary_versions (
    topic_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    summary TEXT NOT NULL,
    participants TEXT NOT NULL DEFAULT '',
    unresolved TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (topic_id, version)
);
CREATE TABLE IF NOT EXISTS group_summary_topic_sources (
    topic_id INTEGER NOT NULL,
    group_message_id INTEGER NOT NULL,
    PRIMARY KEY (topic_id, group_message_id)
);
CREATE TABLE IF NOT EXISTS group_summary_cursors (
    group_id INTEGER PRIMARY KEY,
    applied_message_id INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'persona',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_context_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    layout_version TEXT NOT NULL,
    persona_version TEXT NOT NULL,
    tool_version TEXT NOT NULL,
    active_from_turn_id INTEGER NOT NULL DEFAULT 0,
    group_context_cursor_id INTEGER NOT NULL DEFAULT 0,
    snapshot_version INTEGER NOT NULL DEFAULT 0,
    last_confirmed_turn_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_context_sessions_scope
    ON chat_context_sessions (group_id, user_id);
CREATE TABLE IF NOT EXISTS chat_context_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    item_type TEXT NOT NULL CHECK (item_type IN ('message', 'tool_call', 'tool_result')),
    payload_json TEXT NOT NULL,
    delivery_status TEXT NOT NULL CHECK (delivery_status IN ('confirmed', 'audit')),
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, request_id, sequence),
    FOREIGN KEY(session_id) REFERENCES chat_context_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_context_turns_session
    ON chat_context_turns (session_id, id);
CREATE TABLE IF NOT EXISTS chat_context_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    cutoff_turn_id INTEGER NOT NULL,
    summary_json TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, version),
    UNIQUE(session_id, source_hash),
    FOREIGN KEY(session_id) REFERENCES chat_context_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_context_snapshots_active
    ON chat_context_snapshots (session_id, active, version DESC);
CREATE TABLE IF NOT EXISTS chat_context_compaction_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    cutoff_turn_id INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_until REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    error_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, generation),
    UNIQUE(session_id, source_hash),
    FOREIGN KEY(session_id) REFERENCES chat_context_sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_chat_context_compaction_jobs_ready
    ON chat_context_compaction_jobs (status, next_attempt_at, lease_until, id);
CREATE TABLE IF NOT EXISTS agent_action_executions (
    execution_key TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    state_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'delivered', 'failed', 'stale')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_action_executions_updated
    ON agent_action_executions (updated_at DESC);
"""


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back like sqlite3.Connection, then release Windows locks."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


class TangtangDb:
    """Independent SQLite store for Tangtang interaction history.

    This is a dedicated database file owned by the Tangtang chat module; it
    never touches the existing bot database or its tables.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA + INBOX_SCHEMA)
        self._migrate_schema(conn)
        return conn

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        cursor_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(group_summary_cursors)")
        }
        if cursor_columns and "source" not in cursor_columns:
            conn.execute(
                "ALTER TABLE group_summary_cursors "
                "ADD COLUMN source TEXT NOT NULL DEFAULT 'persona'"
            )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tangtang_group_messages)")
        }
        if "consumed" not in columns:
            conn.execute(
                "ALTER TABLE tangtang_group_messages "
                "ADD COLUMN consumed INTEGER NOT NULL DEFAULT 0"
            )
        call_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tangtang_calls)")
        }
        if "provenance" not in call_columns:
            conn.execute(
                "ALTER TABLE tangtang_calls "
                "ADD COLUMN provenance TEXT NOT NULL DEFAULT ''"
            )
        context_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(chat_context_sessions)")
        }
        if context_columns and "active_from_turn_id" not in context_columns:
            conn.execute(
                "ALTER TABLE chat_context_sessions "
                "ADD COLUMN active_from_turn_id INTEGER NOT NULL DEFAULT 0"
            )
        if context_columns and "group_context_cursor_id" not in context_columns:
            conn.execute(
                "ALTER TABLE chat_context_sessions "
                "ADD COLUMN group_context_cursor_id INTEGER NOT NULL DEFAULT 0"
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
            "(id, group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, "
            "provenance, created_at) "
            "SELECT id, group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, "
            "provenance, created_at "
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
        provenance: Mapping[str, Any] | None = None,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO tangtang_calls "
                "(group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, "
                "provenance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(group_id),
                    int(user_id),
                    str(message_id or ""),
                    call_text,
                    reply_text,
                    reply_kind,
                    mode,
                    json.dumps(dict(provenance or {}), ensure_ascii=False),
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    def insert_reply_parts(
        self,
        call_id: int,
        parts: list[dict[str, Any]],
        *,
        created_at: str,
    ) -> None:
        if not parts:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO tangtang_reply_parts "
                "(call_id, part_index, text, delivered, platform_message_id, error_type, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        int(call_id),
                        int(part["part_index"]),
                        str(part.get("text") or ""),
                        1 if part.get("delivered") else 0,
                        str(part.get("platform_message_id") or ""),
                        str(part.get("error_type") or ""),
                        created_at,
                    )
                    for part in parts
                ],
            )

    def reply_parts(self, call_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tangtang_reply_parts WHERE call_id = ? ORDER BY part_index",
                (int(call_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_self_version(
        self,
        version_hash: str,
        content: str,
        *,
        created_at: str,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM tangtang_self_versions WHERE version_hash = ?",
                (str(version_hash),),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE tangtang_self_versions SET active = 1 WHERE id = ?",
                    (int(row["id"]),),
                )
                conn.execute(
                    "UPDATE tangtang_self_versions SET active = 0 WHERE id <> ?",
                    (int(row["id"]),),
                )
                return
            conn.execute("UPDATE tangtang_self_versions SET active = 0")
            conn.execute(
                "INSERT INTO tangtang_self_versions "
                "(version_hash, content, active, created_at) VALUES (?, ?, 1, ?)",
                (str(version_hash), str(content), created_at),
            )

    def upsert_memory(
        self,
        *,
        group_id: int,
        user_id: int,
        kind: str,
        content: str,
        normalized_content: str,
        status: str,
        importance: float,
        confidence: float,
        source_message_id: str | int,
        source_hash: str,
        now: str,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, evidence_count, status FROM tangtang_memories "
                "WHERE group_id = ? AND user_id = ? AND kind = ? AND source_hash = ?",
                (int(group_id), int(user_id), str(kind), str(source_hash)),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO tangtang_memories "
                    "(group_id, user_id, kind, content, normalized_content, status, "
                    "importance, confidence, source_message_id, source_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(group_id), int(user_id), str(kind), str(content),
                        str(normalized_content), str(status), float(importance),
                        float(confidence), str(source_message_id or ""),
                        str(source_hash), now, now,
                    ),
                )
                return int(cursor.lastrowid)
            evidence_count = int(row["evidence_count"]) + 1
            next_status = str(row["status"])
            if next_status == "candidate" and (status == "active" or evidence_count >= 2):
                next_status = "active"
            conn.execute(
                "UPDATE tangtang_memories SET content = ?, normalized_content = ?, "
                "status = ?, importance = MAX(importance, ?), confidence = MAX(confidence, ?), "
                "evidence_count = ?, source_message_id = ?, updated_at = ?, deleted_at = NULL "
                "WHERE id = ?",
                (
                    str(content), str(normalized_content), next_status,
                    float(importance), float(confidence), evidence_count,
                    str(source_message_id or ""), now, int(row["id"]),
                ),
            )
            return int(row["id"])

    def active_memories(
        self,
        group_id: int,
        user_id: int,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tangtang_memories WHERE group_id = ? AND user_id = ? "
                "AND status = 'active' AND (expires_at IS NULL OR expires_at > datetime('now')) "
                "ORDER BY importance DESC, confidence DESC, updated_at DESC LIMIT ?",
                (int(group_id), int(user_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def forget_memories(
        self,
        group_id: int,
        user_id: int,
        query: str,
        *,
        now: str,
    ) -> int:
        needle = str(query).strip().lower()
        if not needle:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tangtang_memories SET status = 'deleted', deleted_at = ?, updated_at = ? "
                "WHERE group_id = ? AND user_id = ? AND status IN ('candidate', 'active') "
                "AND instr(lower(content), ?) > 0",
                (now, now, int(group_id), int(user_id), needle),
            )
            return int(cursor.rowcount)

    def restore_memories(
        self,
        group_id: int,
        user_id: int,
        query: str,
        *,
        now: str,
    ) -> int:
        needle = str(query).strip().lower()
        if not needle:
            return 0
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE tangtang_memories SET status = 'active', deleted_at = NULL, updated_at = ? "
                "WHERE group_id = ? AND user_id = ? AND status = 'deleted' "
                "AND instr(lower(content), ?) > 0",
                (now, int(group_id), int(user_id), needle),
            )
            return int(cursor.rowcount)

    def relationship_state(self, group_id: int, user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tangtang_relationship_state WHERE group_id = ? AND user_id = ?",
                (int(group_id), int(user_id)),
            ).fetchone()
        return dict(row) if row is not None else {
            "group_id": int(group_id), "user_id": int(user_id),
            "familiarity": 0.0, "warmth": 0.5, "interaction_count": 0,
        }

    def update_relationship_state(
        self,
        group_id: int,
        user_id: int,
        *,
        warmth_delta: float,
        now: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tangtang_relationship_state "
                "(group_id, user_id, updated_at) VALUES (?, ?, ?)",
                (int(group_id), int(user_id), now),
            )
            conn.execute(
                "UPDATE tangtang_relationship_state SET "
                "interaction_count = interaction_count + 1, "
                "familiarity = MIN(1.0, familiarity + 0.01), "
                "warmth = MIN(1.0, MAX(0.0, warmth + ?)), updated_at = ? "
                "WHERE group_id = ? AND user_id = ?",
                (max(-0.03, min(0.03, float(warmth_delta))), now, int(group_id), int(user_id)),
            )

    def persona_state(self, group_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tangtang_persona_state WHERE group_id = ?",
                (int(group_id),),
            ).fetchone()
        return dict(row) if row is not None else {
            "group_id": int(group_id), "valence": 0.5,
            "energy": 0.5, "interaction_count": 0,
        }

    def update_persona_state(
        self,
        group_id: int,
        *,
        valence_delta: float,
        energy_delta: float,
        now: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tangtang_persona_state (group_id, updated_at) VALUES (?, ?)",
                (int(group_id), now),
            )
            old = conn.execute("SELECT valence,energy,updated_at FROM tangtang_persona_state WHERE group_id=?", (int(group_id),)).fetchone()
            conn.execute("UPDATE tangtang_persona_state SET valence=?,energy=? WHERE group_id=?",
                         (mood_decay(old[0], old[2], now), mood_decay(old[1], old[2], now), int(group_id)))
            conn.execute(
                "UPDATE tangtang_persona_state SET interaction_count = interaction_count + 1, "
                "valence = MIN(1.0, MAX(0.0, valence + ?)), "
                "energy = MIN(1.0, MAX(0.0, energy + ?)), updated_at = ? WHERE group_id = ?",
                (
                    max(-0.02, min(0.02, float(valence_delta))),
                    max(-0.02, min(0.02, float(energy_delta))),
                    now,
                    int(group_id),
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

    def proactive_last_attempt(self, group_id: int) -> float:
        with self._connect() as conn:
            row = conn.execute("SELECT last_reply_at FROM tangtang_proactive_state WHERE group_id=?", (group_id,)).fetchone()
            return float(row[0]) if row else 0.0

    def insert_group_message(
        self,
        *,
        group_id: int,
        user_id: int,
        nickname: str,
        text: str,
        message_id: str | int,
        created_at: str,
        observation: dict | None = None,
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
            if observation:
                capture(conn, group_id=int(group_id), user_id=int(user_id),
                        message_id=str(message_id), text=text, **observation)

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

    def group_context_messages(
        self,
        group_id: int,
        *,
        after_id: int = 0,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Latest bounded context after a confirmed session cursor, oldest first."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tangtang_group_messages WHERE group_id = ? AND id > ? "
                "ORDER BY id DESC LIMIT ?",
                (int(group_id), max(0, int(after_id)), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def group_summary_pending(
        self,
        group_id: int,
        *,
        limit: int = 400,
    ) -> list[dict[str, Any]]:
        """Group messages not yet represented by any committed summary batch."""

        with self._connect() as conn:
            state = conn.execute(
                "SELECT applied_message_id FROM group_summary_cursors WHERE group_id = ?",
                (int(group_id),),
            ).fetchone()
            cursor = int(state["applied_message_id"]) if state else 0
            rows = conn.execute(
                "SELECT * FROM tangtang_group_messages "
                "WHERE group_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (int(group_id), cursor, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def group_summary_seed(
        self,
        group_id: int,
        *,
        now: str,
        source: str = "persona",
        reset: bool = False,
    ) -> int:
        """First run starts at the current tail; history is not replayed."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT source FROM group_summary_cursors WHERE group_id = ?",
                    (int(group_id),),
                ).fetchone()
                if (
                    existing is not None
                    and not reset
                    and str(existing["source"] or "") == str(source)
                ):
                    conn.execute("COMMIT")
                    return 0
                row = conn.execute(
                    "SELECT MAX(id) FROM tangtang_group_messages WHERE group_id = ?",
                    (int(group_id),),
                ).fetchone()
                newest = int(row[0] or 0)
                conn.execute(
                    "INSERT INTO group_summary_cursors "
                    "(group_id, applied_message_id, source, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET "
                    "applied_message_id = excluded.applied_message_id, "
                    "source = excluded.source, "
                    "updated_at = excluded.updated_at",
                    (int(group_id), newest, str(source), str(now)),
                )
                conn.execute("COMMIT")
                return newest
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def group_summary_seed_all(
        self,
        group_ids: Iterable[int],
        *,
        now: str,
    ) -> int:
        """Seed every eligible group in one read of the current tails."""

        seeded = 0
        for group_id in group_ids:
            seeded += int(self.group_summary_seed(int(group_id), now=now) > 0)
        return seeded

    def group_summary_skip_older_than(self, group_id: int, cutoff: str) -> int:
        """Advance the cursor over stale rows without generating summaries."""

        with self._connect() as conn:
            cursor_row = conn.execute(
                "SELECT applied_message_id FROM group_summary_cursors WHERE group_id = ?",
                (int(group_id),),
            ).fetchone()
            cursor = int(cursor_row["applied_message_id"]) if cursor_row else 0
            row = conn.execute(
                "SELECT MAX(id) FROM tangtang_group_messages "
                "WHERE group_id = ? AND id > ? AND created_at < ?",
                (int(group_id), cursor, str(cutoff)),
            ).fetchone()
            newest_stale = int(row[0] or 0)
        if newest_stale <= 0:
            return 0
        self.group_summary_advance(
            group_id,
            newest_stale,
            now=datetime.now().astimezone().isoformat(),
        )
        return newest_stale

    def group_summary_archive_excess(self, group_id: int, *, limit: int = 200) -> int:
        """Keep the most active/recent topics; archive the rest for lookup only."""

        maximum = max(1, int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT topic_id FROM group_summary_topics "
                "WHERE group_id = ? AND state != 'archived' "
                "ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, "
                "version DESC, updated_at DESC LIMIT -1 OFFSET ?",
                (int(group_id), maximum),
            ).fetchall()
            topic_ids = [int(row["topic_id"]) for row in rows]
            if not topic_ids:
                return 0
            placeholders = ",".join("?" for _ in topic_ids)
            conn.execute(
                f"UPDATE group_summary_topics SET state = 'archived' "
                f"WHERE topic_id IN ({placeholders})",
                tuple(topic_ids),
            )
        return len(topic_ids)

    def group_summary_advance(self, group_id: int, message_id: int, *, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                    "INSERT INTO group_summary_cursors "
                    "(group_id, applied_message_id, source, updated_at) VALUES (?, ?, 'raw', ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET "
                    "applied_message_id = MAX(applied_message_id, excluded.applied_message_id), "
                    "updated_at = excluded.updated_at",
                (int(group_id), int(message_id), str(now)),
            )

    def group_summary_sources(self, group_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM group_summary_topics "
                "WHERE group_id = ? AND state != 'archived' "
                "ORDER BY updated_at DESC, topic_id DESC",
                (int(group_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def group_summary_matching(
        self,
        group_id: int,
        terms: tuple[str, ...],
        *,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Score persisted topics by term overlap; runtime-only, no model call."""

        rows = self.group_summary_sources(group_id)
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            haystack = f"{row['title']}\n{row['summary']}"
            score = sum(1 for term in terms if term and term in haystack)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        return [row for _score, row in scored[: max(1, int(limit))]]

    def group_summary_merge(
        self,
        group_id: int,
        *,
        topic_id: int | None,
        title: str,
        summary: str,
        keywords: tuple[str, ...],
        participants: tuple[str, ...],
        unresolved: tuple[str, ...],
        state: str,
        message_ids: tuple[int, ...],
        now: str,
    ) -> dict[str, Any]:
        """Atomically write one topic version and its source-message links."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if topic_id is None:
                    cursor = conn.execute(
                        "INSERT INTO group_summary_topics "
                        "(group_id, title, summary, keywords, participants, unresolved, "
                        "state, source_count, version, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (
                            int(group_id),
                            str(title),
                            str(summary),
                            "\n".join(keywords),
                            "\n".join(participants),
                            "\n".join(unresolved),
                            str(state),
                            len(message_ids),
                            str(now),
                            str(now),
                        ),
                    )
                    topic_id = int(cursor.lastrowid)
                else:
                    row = conn.execute(
                        "SELECT version FROM group_summary_topics "
                        "WHERE topic_id = ? AND group_id = ?",
                        (int(topic_id), int(group_id)),
                    ).fetchone()
                    if row is None:
                        raise ValueError("group summary topic vanished")
                    version = int(row["version"]) + 1
                    conn.execute(
                        "UPDATE group_summary_topics SET title = ?, summary = ?, "
                        "keywords = ?, participants = ?, unresolved = ?, state = ?, "
                        "source_count = source_count + ?, version = ?, updated_at = ? "
                        "WHERE topic_id = ? AND group_id = ?",
                        (
                            str(title),
                            str(summary),
                            "\n".join(keywords),
                            "\n".join(participants),
                            "\n".join(unresolved),
                            str(state),
                            len(message_ids),
                            version,
                            str(now),
                            int(topic_id),
                            int(group_id),
                        ),
                    )
                version_row = conn.execute(
                    "SELECT version FROM group_summary_topics WHERE topic_id = ?",
                    (int(topic_id),),
                ).fetchone()
                version = int(version_row["version"]) if version_row else 1
                for message_id in message_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO group_summary_topic_sources "
                        "(topic_id, group_message_id) VALUES (?, ?)",
                        (int(topic_id), int(message_id)),
                    )
                conn.execute(
                    "INSERT INTO group_summary_versions "
                    "(topic_id, version, summary, participants, unresolved, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(topic_id),
                        version,
                        str(summary),
                        "\n".join(participants),
                        "\n".join(unresolved),
                        str(now),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM group_summary_topics WHERE topic_id = ?",
                    (int(topic_id),),
                ).fetchone()
                result = dict(row) if row else {}
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return result

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
                "SELECT id, reply_text FROM tangtang_calls "
                "WHERE group_id = ? ORDER BY id DESC LIMIT ?",
                (int(group_id), int(limit)),
            ).fetchall()
            call_ids = tuple(int(row["id"]) for row in rows)
            part_rows: list[sqlite3.Row] = []
            if call_ids:
                placeholders = ",".join("?" for _ in call_ids)
                part_rows = conn.execute(
                    f"SELECT text FROM tangtang_reply_parts "
                    f"WHERE delivered = 1 AND call_id IN ({placeholders})",
                    call_ids,
                ).fetchall()
        return any(str(row["reply_text"]) == str(text) for row in rows) or any(
            str(row["text"]) == str(text) for row in part_rows
        )

    def model_reply_lines(
        self,
        user_id: int,
        group_id: int,
        limit: int,
        max_chars: int | None = None,
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
        if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
            return text
        return "…" + text[-max_chars:]

    @staticmethod
    def _context_payload(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
        item_type = str(item.get("type") or "message")
        role = str(item.get("role") or ("tool" if item_type == "tool_result" else "assistant"))
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("invalid context item role")
        if item_type not in {"message", "tool_call", "tool_result"}:
            raise ValueError("invalid context item type")
        payload = json.dumps(dict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(payload) > 16_000:
            raise ValueError("context item exceeds 16000 characters")
        source_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return role, item_type, payload, source_hash

    def ensure_context_session(
        self,
        *,
        group_id: int,
        user_id: int,
        layout_version: str,
        persona_version: str,
        tool_version: str,
        now: str,
    ) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_context_sessions WHERE group_id = ? AND user_id = ?",
                (int(group_id), int(user_id)),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO chat_context_sessions "
                    "(group_id, user_id, layout_version, persona_version, tool_version, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(group_id), int(user_id), str(layout_version), str(persona_version),
                     str(tool_version), str(now), str(now)),
                )
                return int(cursor.lastrowid)
            changed = (
                str(row["layout_version"]) != str(layout_version)
                or str(row["persona_version"]) != str(persona_version)
                or str(row["tool_version"]) != str(tool_version)
            )
            active_from = int(row["active_from_turn_id"])
            group_context_cursor = int(row["group_context_cursor_id"])
            if changed:
                latest = conn.execute(
                    "SELECT COALESCE(MAX(id), 0) AS id FROM chat_context_turns "
                    "WHERE session_id = ?",
                    (int(row["id"]),),
                ).fetchone()
                active_from = max(active_from, int(latest["id"]))
                group_context_cursor = 0
            conn.execute(
                "UPDATE chat_context_sessions SET layout_version = ?, persona_version = ?, "
                "tool_version = ?, active_from_turn_id = ?, group_context_cursor_id = ?, "
                "updated_at = ? WHERE id = ?",
                (str(layout_version), str(persona_version), str(tool_version), active_from,
                 group_context_cursor, str(now), int(row["id"])),
            )
        return int(row["id"])

    def context_session(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM chat_context_sessions WHERE id = ?", (int(session_id),)
            ).fetchone()
        return dict(row) if row is not None else None

    def commit_context_items(
        self,
        session_id: int,
        request_id: str,
        items: Iterable[Mapping[str, Any]],
        *,
        now: str,
        delivery_status: str = "confirmed",
        group_context_cursor_id: int | None = None,
    ) -> tuple[int, ...]:
        if delivery_status not in {"confirmed", "audit"}:
            raise ValueError("invalid context delivery status")
        prepared = [self._context_payload(item) for item in items]
        if not prepared:
            return ()
        inserted: list[int] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for sequence, (role, item_type, payload, source_hash) in enumerate(prepared):
                if delivery_status == "confirmed":
                    conn.execute(
                        "INSERT INTO chat_context_turns "
                        "(session_id, request_id, sequence, role, item_type, payload_json, "
                        "delivery_status, source_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(session_id, request_id, sequence) DO UPDATE SET "
                        "role=excluded.role, item_type=excluded.item_type, "
                        "payload_json=excluded.payload_json, delivery_status='confirmed', "
                        "source_hash=excluded.source_hash, created_at=excluded.created_at",
                        (int(session_id), str(request_id), sequence, role, item_type, payload,
                         delivery_status, source_hash, str(now)),
                    )
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO chat_context_turns "
                        "(session_id, request_id, sequence, role, item_type, payload_json, "
                        "delivery_status, source_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (int(session_id), str(request_id), sequence, role, item_type, payload,
                         delivery_status, source_hash, str(now)),
                    )
                row = conn.execute(
                    "SELECT id FROM chat_context_turns "
                    "WHERE session_id = ? AND request_id = ? AND sequence = ?",
                    (int(session_id), str(request_id), sequence),
                ).fetchone()
                inserted.append(int(row["id"]))
            if delivery_status == "confirmed":
                conn.execute(
                    "UPDATE chat_context_sessions SET last_confirmed_turn_id = MAX(last_confirmed_turn_id, ?), "
                    "group_context_cursor_id = MAX(group_context_cursor_id, ?), "
                    "updated_at = ? WHERE id = ?",
                    (
                        max(inserted),
                        max(0, int(group_context_cursor_id or 0)),
                        str(now),
                        int(session_id),
                    ),
                )
        return tuple(inserted)

    def context_window(
        self, session_id: int, *, keep_recent: int | None = None
    ) -> tuple[dict[str, Any] | None, tuple[dict[str, Any], ...]]:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT active_from_turn_id FROM chat_context_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            active_from = int(session["active_from_turn_id"]) if session is not None else 0
            snapshot = conn.execute(
                "SELECT * FROM chat_context_snapshots WHERE session_id = ? AND active = 1 "
                "AND cutoff_turn_id > ? ORDER BY version DESC LIMIT 1",
                (int(session_id), active_from),
            ).fetchone()
            cutoff = max(
                active_from,
                int(snapshot["cutoff_turn_id"]) if snapshot is not None else 0,
            )
            if keep_recent is None:
                rows = conn.execute(
                    "SELECT * FROM chat_context_turns WHERE session_id = ? "
                    "AND delivery_status = 'confirmed' AND id > ? ORDER BY id",
                    (int(session_id), cutoff),
                ).fetchall()
            else:
                limit = max(1, min(int(keep_recent), 100))
                rows = list(reversed(conn.execute(
                    "SELECT * FROM chat_context_turns WHERE session_id = ? "
                    "AND delivery_status = 'confirmed' AND id > ? ORDER BY id DESC LIMIT ?",
                    (int(session_id), cutoff, limit),
                ).fetchall()))
        items = tuple(json.loads(row["payload_json"]) for row in rows)
        snapshot_dict = dict(snapshot) if snapshot is not None else None
        if snapshot_dict is not None:
            snapshot_dict["summary"] = json.loads(snapshot_dict.pop("summary_json"))
        return snapshot_dict, items

    def uncompacted_context_count(self, session_id: int) -> int:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT active_from_turn_id FROM chat_context_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            active_from = int(session["active_from_turn_id"]) if session is not None else 0
            row = conn.execute(
                "SELECT COALESCE(MAX(cutoff_turn_id), 0) AS cutoff FROM chat_context_snapshots "
                "WHERE session_id = ? AND active = 1 AND cutoff_turn_id > ?",
                (int(session_id), active_from),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM chat_context_turns "
                "WHERE session_id = ? AND delivery_status = 'confirmed' AND id > ?",
                (int(session_id), max(active_from, int(row["cutoff"]))),
            ).fetchone()
        return int(count["count"])

    def uncompacted_context_round_count(self, session_id: int) -> int:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT active_from_turn_id FROM chat_context_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            active_from = int(session["active_from_turn_id"]) if session is not None else 0
            row = conn.execute(
                "SELECT COALESCE(MAX(cutoff_turn_id), 0) AS cutoff FROM chat_context_snapshots "
                "WHERE session_id = ? AND active = 1 AND cutoff_turn_id > ?",
                (int(session_id), active_from),
            ).fetchone()
            count = conn.execute(
                "SELECT COUNT(DISTINCT request_id) AS count FROM chat_context_turns "
                "WHERE session_id = ? AND delivery_status = 'confirmed' AND id > ?",
                (int(session_id), max(active_from, int(row["cutoff"]))),
            ).fetchone()
        return int(count["count"])

    def compaction_source(
        self,
        session_id: int,
        *,
        keep_recent: int = 16,
        keep_recent_rounds: int | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT active_from_turn_id FROM chat_context_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            active_from = int(session["active_from_turn_id"]) if session is not None else 0
            snapshot = conn.execute(
                "SELECT * FROM chat_context_snapshots WHERE session_id = ? AND active = 1 "
                "AND cutoff_turn_id > ? ORDER BY version DESC LIMIT 1",
                (int(session_id), active_from),
            ).fetchone()
            cutoff = max(
                active_from,
                int(snapshot["cutoff_turn_id"]) if snapshot is not None else 0,
            )
            rows = conn.execute(
                "SELECT id, request_id, payload_json FROM chat_context_turns "
                "WHERE session_id = ? AND delivery_status = 'confirmed' AND id > ? ORDER BY id",
                (int(session_id), cutoff),
            ).fetchall()
        if keep_recent_rounds is None:
            compact_count = len(rows) - max(1, int(keep_recent))
            if compact_count <= 0:
                return None
            selected = rows[:compact_count]
        else:
            retained_rounds = max(1, int(keep_recent_rounds))
            request_ids = tuple(dict.fromkeys(str(row["request_id"]) for row in rows))
            if len(request_ids) <= retained_rounds:
                return None
            retained = set(request_ids[-retained_rounds:])
            selected = [row for row in rows if str(row["request_id"]) not in retained]
            if not selected:
                return None
        previous = json.loads(snapshot["summary_json"]) if snapshot is not None else None
        source = {
            "previous_snapshot": previous,
            "turns": [{"id": int(row["id"]), "item": json.loads(row["payload_json"])}
                      for row in selected],
        }
        source_json = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "source": source,
            "source_hash": hashlib.sha256(source_json.encode("utf-8")).hexdigest(),
            "cutoff_turn_id": int(selected[-1]["id"]),
        }

    def enqueue_compaction_job(
        self, session_id: int, source_hash: str, cutoff_turn_id: int, *, now: str
    ) -> int:
        with self._connect() as conn:
            session = conn.execute(
                "SELECT snapshot_version FROM chat_context_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
            if session is None:
                raise ValueError("unknown context session")
            generation = int(session["snapshot_version"]) + 1
            existing = conn.execute(
                "SELECT id FROM chat_context_compaction_jobs "
                "WHERE session_id = ? AND generation = ? AND status <> 'done'",
                (int(session_id), generation),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            conn.execute(
                "INSERT OR IGNORE INTO chat_context_compaction_jobs "
                "(session_id, generation, cutoff_turn_id, source_hash, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (int(session_id), generation, int(cutoff_turn_id), str(source_hash), str(now), str(now)),
            )
            row = conn.execute(
                "SELECT id FROM chat_context_compaction_jobs WHERE session_id = ? AND source_hash = ?",
                (int(session_id), str(source_hash)),
            ).fetchone()
        return int(row["id"])

    def claim_compaction_job(
        self, job_id: int, *, owner: str, now_epoch: float, lease_seconds: int, now: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM chat_context_compaction_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if row is None or row["status"] == "done":
                return None
            if float(row["next_attempt_at"]) > float(now_epoch):
                return None
            if row["status"] == "running" and float(row["lease_until"]) > float(now_epoch):
                return None
            cursor = conn.execute(
                "UPDATE chat_context_compaction_jobs SET status = 'running', lease_owner = ?, "
                "lease_until = ?, attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (str(owner), float(now_epoch) + max(1, int(lease_seconds)), str(now), int(job_id)),
            )
            if cursor.rowcount != 1:
                return None
            claimed = conn.execute(
                "SELECT * FROM chat_context_compaction_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
        return dict(claimed)

    def compaction_job_source(self, job_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            job = conn.execute(
                "SELECT * FROM chat_context_compaction_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if job is None:
                return None
            previous = conn.execute(
                "SELECT * FROM chat_context_snapshots WHERE session_id = ? AND version < ? "
                "ORDER BY version DESC LIMIT 1",
                (int(job["session_id"]), int(job["generation"])),
            ).fetchone()
            start = int(previous["cutoff_turn_id"]) if previous is not None else 0
            rows = conn.execute(
                "SELECT id, payload_json FROM chat_context_turns "
                "WHERE session_id = ? AND delivery_status = 'confirmed' AND id > ? AND id <= ? "
                "ORDER BY id",
                (int(job["session_id"]), start, int(job["cutoff_turn_id"])),
            ).fetchall()
        source = {
            "previous_snapshot": (
                json.loads(previous["summary_json"]) if previous is not None else None
            ),
            "turns": [{"id": int(row["id"]), "item": json.loads(row["payload_json"])}
                      for row in rows],
        }
        encoded = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != str(job["source_hash"]):
            raise ValueError("compaction source hash mismatch")
        return source

    def complete_compaction_job(
        self, job_id: int, *, owner: str, summary: Mapping[str, Any], now: str
    ) -> int:
        summary_json = json.dumps(dict(summary), ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT * FROM chat_context_compaction_jobs WHERE id = ?", (int(job_id),)
            ).fetchone()
            if job is None or job["status"] != "running" or job["lease_owner"] != str(owner):
                raise ValueError("compaction lease is not owned")
            conn.execute(
                "UPDATE chat_context_snapshots SET active = 0 WHERE session_id = ?",
                (int(job["session_id"]),),
            )
            conn.execute(
                "INSERT OR IGNORE INTO chat_context_snapshots "
                "(session_id, version, cutoff_turn_id, summary_json, source_hash, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (int(job["session_id"]), int(job["generation"]), int(job["cutoff_turn_id"]),
                 summary_json, str(job["source_hash"]), str(now)),
            )
            conn.execute(
                "UPDATE chat_context_snapshots SET active = 1 "
                "WHERE session_id = ? AND source_hash = ?",
                (int(job["session_id"]), str(job["source_hash"])),
            )
            conn.execute(
                "UPDATE chat_context_sessions SET snapshot_version = MAX(snapshot_version, ?), "
                "updated_at = ? WHERE id = ?",
                (int(job["generation"]), str(now), int(job["session_id"])),
            )
            conn.execute(
                "UPDATE chat_context_compaction_jobs SET status = 'done', lease_until = 0, "
                "error_reason = '', updated_at = ? WHERE id = ?",
                (str(now), int(job_id)),
            )
        return int(job["generation"])

    def fail_compaction_job(
        self, job_id: int, *, owner: str, reason: str, now_epoch: float, now: str
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT attempts FROM chat_context_compaction_jobs "
                "WHERE id = ? AND status = 'running' AND lease_owner = ?",
                (int(job_id), str(owner)),
            ).fetchone()
            if row is None:
                return
            delay = min(3600, 30 * (2 ** min(int(row["attempts"]), 7)))
            conn.execute(
                "UPDATE chat_context_compaction_jobs SET status = 'failed', lease_until = 0, "
                "next_attempt_at = ?, error_reason = ?, updated_at = ? WHERE id = ?",
                (float(now_epoch) + delay, str(reason)[:300], str(now), int(job_id)),
            )

    def claim_action_execution(
        self, execution_key: str, *, action: str, state_version: str, now: str
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO agent_action_executions "
                "(execution_key, action, state_version, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'reserved', ?, ?)",
                (str(execution_key), str(action), str(state_version), str(now), str(now)),
            )
        return cursor.rowcount == 1

    def finish_action_execution(self, execution_key: str, *, status: str, now: str) -> None:
        if status not in {"delivered", "failed", "stale"}:
            raise ValueError("invalid action execution status")
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_action_executions SET status = ?, updated_at = ? "
                "WHERE execution_key = ? AND status = 'reserved'",
                (str(status), str(now), str(execution_key)),
            )


def _local_hour(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(settings.timezone))
    return dt.hour
