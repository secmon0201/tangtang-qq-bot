from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence
from zoneinfo import ZoneInfo

from bot.config import ROOT, settings


DEFAULT_DB_PATH = ROOT / "data" / "knowledge" / "knowledge.db"
ZHIJIANG_RESOURCE_PATH = ROOT / "bot" / "resources" / "zhijiang_encyclopedia.json"
MINGCHAO_RESOURCE_PATH = ROOT / "bot" / "resources" / "mingchao_meme_culture.json"
MINGCHAO_OFFICIAL_RESOURCE_PATH = (
    ROOT / "bot" / "resources" / "mingchao_official_knowledge.json"
)

FORBIDDEN_LOCAL_TERMS = ("珈乐", "皇珈骑士", "皇珈")
VALID_DOMAINS = frozenset({"zhijiang", "mingchao"})
_ENTRY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _title_key(title: str) -> str:
    return re.sub(r"\s+", "", title or "").lower()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    source_name TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    source_note TEXT NOT NULL DEFAULT '',
    blocked INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'approved',
    source_priority INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    conflict_note TEXT NOT NULL DEFAULT '',
    related_ids TEXT NOT NULL DEFAULT '[]',
    UNIQUE(domain, entry_id)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_domain_status
    ON knowledge_entries (domain, status);
"""


def _now() -> str:
    return datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")


class KnowledgeDb:
    """SQLite store for curated local knowledge (zhijiang / mingchao).

    JSON resource files act as the initial seed; the SQLite table is the
    operational store. Web-sourced proposals enter as status='pending' and
    only become searchable after an admin approves them.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DB_PATH
        self._cache: dict[str, tuple[dict[str, Any], ...]] = {}

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        self._migrate_schema(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _migrate_schema(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(knowledge_entries)")
        }
        if columns and "related_ids" not in columns:
            conn.execute(
                "ALTER TABLE knowledge_entries "
                "ADD COLUMN related_ids TEXT NOT NULL DEFAULT '[]'"
            )

    def _invalidate(self) -> None:
        self._cache.clear()

    def _ensure_seeded(self, conn: sqlite3.Connection, domain: str) -> None:
        existing = {
            row["entry_id"]
            for row in conn.execute(
                "SELECT entry_id FROM knowledge_entries WHERE domain = ?",
                (domain,),
            )
        }
        paths = (
            (ZHIJIANG_RESOURCE_PATH,)
            if domain == "zhijiang"
            else (MINGCHAO_RESOURCE_PATH, MINGCHAO_OFFICIAL_RESOURCE_PATH)
        )
        for path in paths:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                raise ValueError(f"{path.name} must be a JSON array")
            updated_at = datetime.fromtimestamp(
                path.stat().st_mtime, ZoneInfo(settings.timezone)
            ).isoformat(timespec="seconds")
            for item in data:
                if not isinstance(item, dict):
                    continue
                entry_id = str(item.get("id") or "").strip()
                if not entry_id:
                    continue
                related_ids = json.dumps(item.get("related_ids", []), ensure_ascii=False)
                values = (
                    entry_id,
                    domain,
                    str(item.get("category") or ""),
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    json.dumps(item.get("tags", []), ensure_ascii=False),
                    str(item.get("source_name") or ""),
                    str(item.get("source_url") or ""),
                    str(item.get("source_note") or ""),
                    1 if item.get("blocked") else 0,
                    updated_at,
                    related_ids,
                )
                if entry_id in existing:
                    # Curated seed rows stay in sync with JSON resources; rows
                    # approved from web proposals are never overwritten.
                    conn.execute(
                        "UPDATE knowledge_entries SET category = ?, title = ?, "
                        "summary = ?, tags = ?, source_name = ?, source_url = ?, "
                        "source_note = ?, blocked = ?, updated_at = ?, related_ids = ? "
                        "WHERE domain = ? AND entry_id = ? AND source_priority = 0 "
                        "AND status = 'approved'",
                        values[2:] + (domain, entry_id),
                    )
                    continue
                conn.execute(
                    "INSERT INTO knowledge_entries "
                    "(entry_id, domain, category, title, summary, tags, source_name, "
                    " source_url, source_note, blocked, status, source_priority, "
                    " updated_at, conflict_note, related_ids) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', 0, ?, '', ?)",
                    values,
                )
                existing.add(entry_id)

    def approved_entries(self, domain: str) -> tuple[dict[str, Any], ...]:
        """Approved, searchable entries for one domain (process-cached)."""

        if domain not in VALID_DOMAINS:
            raise ValueError(f"unknown knowledge domain: {domain}")
        if domain not in self._cache:
            with self._connect() as conn:
                self._ensure_seeded(conn, domain)
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries "
                    "WHERE domain = ? AND status = 'approved' ORDER BY entry_id",
                    (domain,),
                ).fetchall()
            self._cache[domain] = tuple(dict(row) for row in rows)
        return self._cache[domain]

    def find_approved(self, domain: str, entry_id: str) -> dict[str, Any] | None:
        """Look up one approved entry (used for cross-domain references)."""

        for row in self.approved_entries(domain):
            if row["entry_id"] == entry_id:
                return row
        return None

    def propose_entry(
        self,
        *,
        domain: str,
        entry_id: str,
        title: str,
        summary: str,
        tags: Sequence[str] = (),
        category: str = "",
        source_name: str,
        source_url: str,
        source_note: str = "",
        conflict_note: str = "",
    ) -> int:
        """Insert a web-sourced proposal as status='pending' (never searchable)."""

        if domain not in VALID_DOMAINS:
            raise ValueError(f"unknown knowledge domain: {domain}")
        entry_id = entry_id.strip()
        title = title.strip()
        summary = summary.strip()
        source_name = source_name.strip()
        source_url = source_url.strip()
        if not _ENTRY_ID_RE.fullmatch(entry_id):
            raise ValueError("entry_id must be lowercase letters/digits/hyphens")
        if not title or not summary or not source_name or not source_url:
            raise ValueError("title, summary, source_name and source_url are required")
        if not source_url.startswith(("http://", "https://")):
            raise ValueError("source_url must be an http(s) URL")
        combined = " ".join(
            (title, summary, source_name, source_note, conflict_note, category)
        )
        if any(term in combined for term in FORBIDDEN_LOCAL_TERMS):
            raise ValueError("proposal contains forbidden content and was rejected")
        with self._connect() as conn:
            self._ensure_seeded(conn, domain)
            same_id = conn.execute(
                "SELECT status FROM knowledge_entries WHERE domain = ? AND entry_id = ?",
                (domain, entry_id),
            ).fetchone()
            if same_id is not None:
                if same_id["status"] == "rejected":
                    raise ValueError(f"previously rejected: {domain}/{entry_id}")
                raise ValueError(f"entry already exists: {domain}/{entry_id}")
            for status in ("approved", "pending", "rejected"):
                title_rows = conn.execute(
                    "SELECT title FROM knowledge_entries WHERE domain = ? AND status = ?",
                    (domain, status),
                ).fetchall()
                for title_row in title_rows:
                    if _title_key(title_row["title"]) == _title_key(title):
                        if status == "rejected":
                            raise ValueError(f"previously rejected: {domain}/{title}")
                        raise ValueError(
                            f"entry with the same title already exists: {domain}/{title}"
                        )
            cursor = conn.execute(
                "INSERT INTO knowledge_entries "
                "(entry_id, domain, category, title, summary, tags, source_name, "
                " source_url, source_note, blocked, status, source_priority, "
                " updated_at, conflict_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', 10, ?, ?)",
                (
                    entry_id,
                    domain,
                    category.strip(),
                    title,
                    summary,
                    json.dumps([str(tag) for tag in tags], ensure_ascii=False),
                    source_name,
                    source_url,
                    source_note.strip(),
                    _now(),
                    conflict_note.strip(),
                ),
            )
            row_id = int(cursor.lastrowid)
        self._invalidate()
        return row_id

    def pending_entries(self, domain: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if domain is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries WHERE status = 'pending' ORDER BY id"
                ).fetchall()
            else:
                if domain not in VALID_DOMAINS:
                    raise ValueError(f"unknown knowledge domain: {domain}")
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries "
                    "WHERE domain = ? AND status = 'pending' ORDER BY id",
                    (domain,),
                ).fetchall()
        return [dict(row) for row in rows]

    def rejected_entries(self, domain: str | None = None) -> list[dict[str, Any]]:
        """Rejected proposals kept for duplicate rejection and statistics."""

        with self._connect() as conn:
            if domain is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries WHERE status = 'rejected' ORDER BY id"
                ).fetchall()
            else:
                if domain not in VALID_DOMAINS:
                    raise ValueError(f"unknown knowledge domain: {domain}")
                rows = conn.execute(
                    "SELECT * FROM knowledge_entries "
                    "WHERE domain = ? AND status = 'rejected' ORDER BY id",
                    (domain,),
                ).fetchall()
        return [dict(row) for row in rows]

    def find_pending(self, domain: str, entry_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_entries "
                "WHERE domain = ? AND entry_id = ? AND status = 'pending'",
                (domain, entry_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def find_pending_by_title(self, domain: str, title: str) -> dict[str, Any] | None:
        key = _title_key(title)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_entries "
                "WHERE domain = ? AND status = 'pending'",
                (domain,),
            ).fetchall()
        for row in rows:
            if _title_key(row["title"]) == key:
                return dict(row)
        return None

    def approve_entry(
        self,
        entry_id: int | str,
        conflict_note: str | None = None,
    ) -> dict[str, Any]:
        """Promote a pending proposal to approved so local search can see it."""

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ? AND status = 'pending'",
                (int(entry_id),),
            ).fetchone()
            if row is None:
                raise ValueError(f"pending entry #{entry_id} not found")
            priority = 1 if row["source_priority"] >= 10 else row["source_priority"]
            note = row["conflict_note"] if conflict_note is None else conflict_note
            conn.execute(
                "UPDATE knowledge_entries "
                "SET status = 'approved', source_priority = ?, updated_at = ?, "
                "    conflict_note = ? "
                "WHERE id = ?",
                (priority, _now(), note, int(entry_id)),
            )
            result = dict(row)
        self._invalidate()
        return result

    def reject_entry(self, entry_id: int | str) -> None:
        """Archive a pending proposal as rejected (never searchable, counted)."""

        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_entries SET status = 'rejected', updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (_now(), int(entry_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"pending entry #{entry_id} not found")
        self._invalidate()

    def revive_entry(self, entry_id: int | str) -> dict[str, Any]:
        """Move an archived rejection back to pending for a fresh review."""

        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE knowledge_entries SET status = 'pending', "
                "source_priority = 10, updated_at = ? "
                "WHERE id = ? AND status = 'rejected'",
                (_now(), int(entry_id)),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"rejected entry #{entry_id} not found")
            row = conn.execute(
                "SELECT * FROM knowledge_entries WHERE id = ?",
                (int(entry_id),),
            ).fetchone()
            result = dict(row)
        self._invalidate()
        return result
