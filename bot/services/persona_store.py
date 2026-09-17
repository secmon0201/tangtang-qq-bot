"""Persona selection, delivered evidence, budgets and delivery journal in SQLite."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo


SCHEMA = """
CREATE TABLE IF NOT EXISTS selections(group_id INTEGER PRIMARY KEY, persona TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS claims(request_id TEXT PRIMARY KEY, claimed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS options(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY, persona TEXT NOT NULL,
 group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, request_id TEXT NOT NULL,
 source TEXT NOT NULL, reply TEXT NOT NULL, day TEXT NOT NULL, created_at REAL NOT NULL,
 processed INTEGER NOT NULL DEFAULT 0, UNIQUE(persona,group_id,request_id));
CREATE TABLE IF NOT EXISTS growth(id INTEGER PRIMARY KEY, persona TEXT NOT NULL,
 group_id INTEGER NOT NULL, topic TEXT NOT NULL, content TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
 UNIQUE(persona,group_id,topic));
CREATE TABLE IF NOT EXISTS growth_versions(entry_id INTEGER NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, evidence_ids TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(entry_id,version));
CREATE TABLE IF NOT EXISTS growth_hidden(group_id INTEGER NOT NULL, entry_id INTEGER NOT NULL,
 PRIMARY KEY(group_id,entry_id));
CREATE TABLE IF NOT EXISTS budgets(day TEXT NOT NULL, kind TEXT NOT NULL,
 scope TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY(day,kind,scope));
CREATE TABLE IF NOT EXISTS growth_reviews(id INTEGER PRIMARY KEY, persona TEXT NOT NULL,
 group_id INTEGER NOT NULL, created_at REAL NOT NULL, outcome TEXT NOT NULL,
 evidence_ids TEXT NOT NULL, decisions TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_persona_recent ON evidence(persona,id DESC);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, group_id INTEGER,
 created_at REAL NOT NULL, usage TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries(request_id TEXT PRIMARY KEY, persona TEXT NOT NULL,
 group_id INTEGER NOT NULL, status TEXT NOT NULL, transcript TEXT NOT NULL,
 platform_message_id TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS topics(id INTEGER PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, published_at TEXT NOT NULL, fetched_at REAL NOT NULL,
 content_hash TEXT NOT NULL, UNIQUE(source,url));
CREATE TABLE IF NOT EXISTS topic_use(persona TEXT NOT NULL, group_id INTEGER NOT NULL,
 topic_id INTEGER NOT NULL, used_at REAL NOT NULL, PRIMARY KEY(persona,group_id,topic_id));
CREATE TABLE IF NOT EXISTS expression_catalog(persona TEXT NOT NULL, expression_id TEXT NOT NULL,
 name TEXT NOT NULL, file TEXT NOT NULL, use_case TEXT NOT NULL, avoid_case TEXT NOT NULL,
 PRIMARY KEY(persona, expression_id));
CREATE TABLE IF NOT EXISTS expression_metadata(persona TEXT NOT NULL, expression_id TEXT NOT NULL,
 version TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(persona, expression_id));
CREATE TABLE IF NOT EXISTS expression_events(persona TEXT NOT NULL, request_id TEXT NOT NULL,
 group_id INTEGER NOT NULL, created_at REAL NOT NULL, completed_at REAL,
 selected_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
 decision_json TEXT NOT NULL, platform_message_id TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(persona, request_id));
CREATE INDEX IF NOT EXISTS expression_delivered_scope
 ON expression_events(persona,group_id,status,completed_at);
"""


class PersonaStore:
    def __init__(self, path: Path, timezone: str = "Asia/Shanghai") -> None:
        self.path = path
        self.timezone = ZoneInfo(timezone)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.timezone).date().isoformat()

    def selection(self, group_id: int) -> tuple[str, int]:
        with self.connect() as conn:
            row = conn.execute("SELECT persona,revision FROM selections WHERE group_id=?", (group_id,)).fetchone()
        return (str(row[0]), int(row[1])) if row else ("tangtang", 0)

    def claim_request(self, request_id: str, now: float) -> bool:
        return self.claim_requests((request_id,), now)

    def request_claimed(self, request_id: str, now: float) -> bool:
        """Read the shared replay guard without reserving a future model turn."""
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM claims WHERE request_id=? AND claimed_at>=?",
                                (request_id, now - 86400)).fetchone() is not None

    def claim_requests(self, request_ids: tuple[str, ...], now: float) -> bool:
        ids = tuple(dict.fromkeys(request_ids))
        if not ids:
            return False
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if any(conn.execute("SELECT 1 FROM claims WHERE request_id=? AND claimed_at>=?",
                                (key, now - 86400)).fetchone() for key in ids):
                return False
            conn.executemany("INSERT INTO claims VALUES(?,?) ON CONFLICT(request_id) DO UPDATE SET claimed_at=excluded.claimed_at",
                             ((key, now) for key in ids))
            return True

    def switch(self, group_id: int, persona: str) -> int:
        if persona not in {"tangtang", "denia"}:
            raise ValueError("unknown persona")
        with self.connect() as conn:
            conn.execute("INSERT INTO selections VALUES(?,?,1) ON CONFLICT(group_id) DO UPDATE SET persona=excluded.persona,revision=revision+1", (group_id, persona))
        return self.selection(group_id)[1]

    def option(self, key: str, default=None):
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM options WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_option(self, key: str, value, *, invalidate: bool = True) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO options VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))
            if invalidate:
                conn.execute("INSERT INTO options VALUES('revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")

    def revision(self) -> int:
        return int(self.option("revision", 0))

    def sync_expression_catalog(self, persona: str, rows: list[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO expression_catalog(persona,expression_id,name,file,use_case,avoid_case) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(persona,expression_id) DO UPDATE SET name=excluded.name,file=excluded.file,use_case=excluded.use_case,avoid_case=excluded.avoid_case",
                [(persona, str(r["id"]), str(r["name"]), str(r["file"]), str(r["use"]), str(r["avoid"])) for r in rows],
            )

    def observe(self, *, persona: str, group_id: int, user_id: int, request_id: str,
                source: str, reply: str, now: float) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO evidence(persona,group_id,user_id,request_id,source,reply,day,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (persona, group_id, user_id, request_id, source[:2000], reply[:2000], self.day(now), now))

    def pending_groups(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT persona,group_id,COUNT(*) AS count,MIN(created_at) AS oldest FROM evidence WHERE processed=0 GROUP BY persona,group_id HAVING COUNT(*)>=5 ORDER BY oldest")]

    def interactions(self, persona: str, group_id: int, *, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM evidence WHERE persona=? AND (?=0 OR group_id=?) ORDER BY id DESC LIMIT ?", (persona, group_id, group_id, limit))]

    def mark_processed(self, ids: list[int]) -> None:
        with self.connect() as conn:
            conn.executemany("UPDATE evidence SET processed=1 WHERE id=?", ((int(i),) for i in ids))

    def pending_interactions(self, persona: str, group_id: int, *, limit: int = 10) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM evidence WHERE persona=? AND group_id=? AND processed=0 ORDER BY id LIMIT ?",
                (persona, group_id, limit),
            )]

    def cited_interactions(self, persona: str, group_id: int, ids: list[int]) -> list[dict]:
        ids = list(dict.fromkeys(ids))[:20]
        if not ids:
            return []
        with self.connect() as conn:
            placeholders = ",".join("?" for _ in ids)
            return [dict(r) for r in conn.execute(
                f"SELECT * FROM evidence WHERE persona=? AND (?=0 OR group_id=?) AND id IN ({placeholders})",
                (persona, group_id, group_id, *ids),
            )]

    def claim_budget(self, kind: str, group_id: int, now: float, global_limit: int,
                     group_limit: int, *, paced: bool = False, category: str = "", category_limit: int = 0) -> bool:
        day = self.day(now)
        if paced:
            # Cumulative release: 3/6/9/12 by six-hour window at the default cap.
            window = datetime.fromtimestamp(now, self.timezone).hour // 6 + 1
            global_limit = (global_limit * window + 3) // 4
        limits = [("global", global_limit), (f"group:{group_id}", group_limit)]
        if category:
            limits.append((f"category:{category}", category_limit))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if paced and conn.execute("SELECT 1 FROM jobs WHERE kind='growth' AND group_id=? AND created_at>? LIMIT 1", (group_id, now - 21600)).fetchone():
                return False
            for scope, limit in limits:
                row = conn.execute("SELECT used FROM budgets WHERE day=? AND kind=? AND scope=?", (day, kind, scope)).fetchone()
                if (int(row[0]) if row else 0) >= limit:
                    return False
            for scope, _ in limits:
                conn.execute("INSERT INTO budgets VALUES(?,?,?,1) ON CONFLICT(day,kind,scope) DO UPDATE SET used=used+1", (day, kind, scope))
        return True

    def budget_used(self, kind: str, scope: str, now: float) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT used FROM budgets WHERE day=? AND kind=? AND scope=?", (self.day(now), kind, scope)).fetchone()
        return int(row[0]) if row else 0

    def journal(self, request_id: str, persona: str, group_id: int, status: str,
                transcript: str, message_id: str = "", detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO deliveries VALUES(?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET status=excluded.status,platform_message_id=excluded.platform_message_id,detail=excluded.detail",
                         (request_id, persona, group_id, status, transcript, message_id, detail))

    def delivery(self, request_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM deliveries WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else None

    def record_job(self, kind: str, group_id: int, now: float, usage: dict, status: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO jobs(kind,group_id,created_at,usage,status) VALUES(?,?,?,?,?)", (kind, group_id, now, json.dumps(usage), status))

    def growth_review(self, persona: str, group_id: int, now: float, outcome: str,
                      evidence_ids: list[int], decisions: list[dict]) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO growth_reviews(persona,group_id,created_at,outcome,evidence_ids,decisions) VALUES(?,?,?,?,?,?)",
                         (persona, group_id, now, outcome, json.dumps(evidence_ids), json.dumps(decisions, ensure_ascii=False)))

    def growth_diagnostics(self, persona: str, group_id: int, limit: int = 5) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM growth_reviews WHERE persona=? AND (?=0 OR group_id=?) ORDER BY id DESC LIMIT ?", (persona, group_id, group_id, limit))]

    def last_growth_attempt(self, group_id: int) -> float:
        with self.connect() as conn:
            return float(conn.execute("SELECT COALESCE(MAX(created_at),0) FROM jobs WHERE kind='growth' AND group_id=?", (group_id,)).fetchone()[0])
