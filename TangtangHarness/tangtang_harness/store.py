"""SQLite storage owned entirely by TangtangHarness."""
from __future__ import annotations

import json
import hashlib
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .types import ChatResponse, InboundEvent, ToolCall, ToolResult
from .redaction import redact_payload
from .message_text import normalize_voice


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL, session_key TEXT NOT NULL,
 event_id TEXT NOT NULL, user_id INTEGER NOT NULL, timestamp REAL NOT NULL,
 received_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_session ON events(session_key,id);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id,id);
CREATE INDEX IF NOT EXISTS events_session_user ON events(session_key,user_id,id);
CREATE TABLE IF NOT EXISTS sessions (
 session_key TEXT PRIMARY KEY, updated_at REAL NOT NULL, context_cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS turns (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, event_key TEXT NOT NULL UNIQUE,
 request_id TEXT NOT NULL, user_content TEXT NOT NULL, messages TEXT NOT NULL,
 message_ids TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_key,id);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, session_key TEXT NOT NULL, event_key TEXT NOT NULL,
 profile_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, api_style TEXT NOT NULL,
 purpose TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'unknown',
 payload TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL,
 error TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, ended_at REAL,
 snapshot_revision INTEGER NOT NULL DEFAULT 0, telemetry TEXT NOT NULL DEFAULT '{}',
 response TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS requests_session ON requests(session_key,started_at);
CREATE INDEX IF NOT EXISTS requests_started ON requests(started_at);
CREATE INDEX IF NOT EXISTS requests_profile_time ON requests(profile_id,started_at);
CREATE INDEX IF NOT EXISTS requests_purpose_time ON requests(purpose,started_at);
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, session_key TEXT NOT NULL,
 event_key TEXT NOT NULL, outcome TEXT NOT NULL, message_ids TEXT NOT NULL,
 messages TEXT NOT NULL, error TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, revision INTEGER NOT NULL,
 cutoff_turn_id INTEGER NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL,
 UNIQUE(session_key,revision));
CREATE TABLE IF NOT EXISTS tools (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, event_key TEXT NOT NULL,
 name TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
 origin TEXT PRIMARY KEY, source_hash TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS background_jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, session_key TEXT NOT NULL,
 status TEXT NOT NULL, source TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
 created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS assets (
 digest TEXT PRIMARY KEY, media_type TEXT NOT NULL, bytes BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memory_records (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
 content TEXT NOT NULL, quote TEXT NOT NULL, event_key TEXT NOT NULL, status TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(session_key,user_id,content));
CREATE TABLE IF NOT EXISTS memory_versions (
 id INTEGER PRIMARY KEY, memory_id INTEGER NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS impression_events (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, user_id INTEGER NOT NULL,
 event_key TEXT NOT NULL, trait TEXT NOT NULL, direction INTEGER NOT NULL,
 quote TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(session_key,user_id,event_key,trait));
CREATE TABLE IF NOT EXISTS cognition_records (
 id INTEGER PRIMARY KEY, session_key TEXT NOT NULL, user_id INTEGER NOT NULL,
 kind TEXT NOT NULL, topic TEXT NOT NULL, content TEXT NOT NULL, state TEXT NOT NULL,
 quote TEXT NOT NULL, event_key TEXT NOT NULL, version INTEGER NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(session_key,user_id,kind,topic));
CREATE TABLE IF NOT EXISTS cognition_versions (
 id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, state TEXT NOT NULL, quote TEXT NOT NULL, event_key TEXT NOT NULL,
 created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS growth_records (
 id INTEGER PRIMARY KEY, scope TEXT NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL,
 status TEXT NOT NULL, version INTEGER NOT NULL, event_key TEXT NOT NULL, quote TEXT NOT NULL,
 created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(scope,content));
CREATE TABLE IF NOT EXISTS growth_versions (
 id INTEGER PRIMARY KEY, growth_id INTEGER NOT NULL, version INTEGER NOT NULL,
 content TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_timings (
 job_id TEXT PRIMARY KEY, started_at REAL, ended_at REAL,
 state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS summary_versions (
 session_key TEXT NOT NULL, revision INTEGER NOT NULL, content TEXT NOT NULL,
 cutoff_event_id INTEGER NOT NULL, source_users TEXT NOT NULL, job_id TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(session_key,revision));
CREATE TABLE IF NOT EXISTS delivery_timings (
 delivery_id INTEGER PRIMARY KEY, started_at REAL NOT NULL,
 ended_at REAL NOT NULL, elapsed_ms REAL NOT NULL);
"""


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("payload", "usage", "telemetry", "messages", "message_ids", "response",
                "user_content", "arguments", "result", "content", "source"):
        if key in result:
            result[key] = json.loads(result[key])
            if key in {"messages", "user_content", "source", "content", "result"}:
                result[key] = normalize_voice(result[key])
    if "event_id" in result and isinstance(result.get("payload"), dict):
        result["payload"] = InboundEvent.from_dict(result["payload"]).to_dict()
    return result


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root: Path, *, path: Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.path = path or self.root / "data" / "harness.db"
        if not self.path.resolve().is_relative_to(self.root):
            raise ValueError("Harness 数据库必须位于新目录内")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def append_event(self, event: InboundEvent) -> bool:
        now = time.time()
        with self.connect() as conn:
            row = conn.execute("INSERT OR IGNORE INTO events(event_key,session_key,event_id,user_id,timestamp,received_at,payload) VALUES(?,?,?,?,?,?,?)",
                (event.key, event.session_key, event.event_id, event.user_id, event.timestamp,
                 now, encode(event.to_dict())))
            if not row.rowcount:
                return False
            conn.execute("INSERT INTO sessions VALUES(?,?,0) ON CONFLICT(session_key) DO UPDATE SET updated_at=excluded.updated_at",
                         (event.session_key, now))
        return True

    def events(self, session_key: str | None = None, limit: int = 100, *, after_id: int = 0) -> list[dict[str, Any]]:
        clause = "session_key=? AND id>?" if session_key else "id>?"
        args = (session_key, after_id) if session_key else (after_id,)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM events WHERE {clause} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [decode_row(row) for row in reversed(rows)]

    def own_events(self, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [decode_row(row) for row in reversed(rows)]

    def user_events(self, session_key: str, user_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM events WHERE session_key=? AND user_id=? ORDER BY id DESC LIMIT ?', (session_key, user_id, limit)).fetchall()
        return [decode_row(row) for row in reversed(rows)]

    def event(self, event_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return decode_row(conn.execute("SELECT * FROM events WHERE event_key=?", (event_key,)).fetchone())

    def sessions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC")]

    def session(self, session_key: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_key=?", (session_key,)).fetchone()
        return dict(row) if row else {"session_key": session_key, "context_cursor": 0}

    def history(self, session_key: str, *, after_id: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        sql = """SELECT t.*,e.user_id AS source_user_id,
            COALESCE(json_extract(e.payload,'$.quoted.user_id'),json_extract(e.payload,'$.quoted.sender.user_id')) AS quote_user_id,
            COALESCE(json_extract(s.value,'$.chat_allowed'),1) AS chat_allowed
            FROM turns t LEFT JOIN events e ON e.event_key=t.event_key
            LEFT JOIN settings s ON s.key='event_scope:'||t.event_key
            WHERE t.session_key=? AND t.id>? AND t.status='delivered'"""
        with self.connect() as conn:
            if limit is None:
                rows = conn.execute(sql + " ORDER BY t.id", (session_key, after_id)).fetchall()
            else:
                rows = list(reversed(conn.execute(sql + " ORDER BY t.id DESC LIMIT ?", (session_key, after_id, limit)).fetchall()))
        return [decode_row(row) for row in rows]

    def excluded_turn_ids(self, session_key: str, blocked: set[int]) -> set[int]:
        """Check source visibility in one query without loading historical image bodies."""
        sql = """SELECT t.id FROM turns t LEFT JOIN events e ON e.event_key=t.event_key
            LEFT JOIN settings s ON s.key='event_scope:'||t.event_key
            WHERE t.session_key=? AND t.status='delivered' AND
            (COALESCE(json_extract(s.value,'$.chat_allowed'),1)=0"""
        args: list[Any] = [session_key]
        if blocked:
            sql += """ OR e.user_id IS NULL OR e.user_id=0 OR EXISTS(
                SELECT 1 FROM json_each(?) b WHERE e.user_id=CAST(b.value AS INTEGER)
                OR CAST(COALESCE(json_extract(e.payload,'$.quoted.user_id'),json_extract(e.payload,'$.quoted.sender.user_id')) AS INTEGER)=CAST(b.value AS INTEGER)
                OR instr(t.user_content,'（'||b.value||'）')>0)"""
            args.append(encode(sorted(blocked)))
        with self.connect() as conn:
            return {row['id'] for row in conn.execute(sql + ')', args)}

    def confirm_turn(self, event: InboundEvent, messages: list[str], request_id: str = "",
                     message_ids: tuple[str, ...] | list[str] = (), user_content: Any = None,
                     *, context_cursor: int | None = None) -> int:
        if not message_ids or any(not str(mid) for mid in message_ids):
            raise ValueError("只有真实 QQ 消息回执可以确认送达")
        self.append_event(event)
        with self.connect() as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO turns(session_key,event_key,request_id,user_content,messages,message_ids,status,created_at) VALUES(?,?,?,?,?,?,'delivered',?)",
                         (event.session_key, event.key, request_id, encode(normalize_voice(user_content if user_content is not None else event.text)),
                          encode(normalize_voice(messages)), encode([str(mid) for mid in message_ids]), time.time()))
            turn = conn.execute("SELECT id FROM turns WHERE event_key=?", (event.key,)).fetchone()
            if context_cursor is not None:
                conn.execute("UPDATE sessions SET context_cursor=MAX(context_cursor,?) WHERE session_key=?", (context_cursor, event.session_key))
            if inserted.rowcount:
                conn.execute("INSERT INTO deliveries(request_id,session_key,event_key,outcome,message_ids,messages,error,created_at) VALUES(?,?,?,'delivered',?,?,?,?)",
                             (request_id, event.session_key, event.key, encode(list(message_ids)), encode(normalize_voice(messages)), "", time.time()))
        return int(turn["id"])

    def confirm_response(self, response: ChatResponse, event: InboundEvent, message_ids: list[str]) -> int:
        row = self.request(response.request_id)
        cursor = (row or {}).get("telemetry", {}).get("context_cursor")
        return self.confirm_turn(event, response.messages, response.request_id, message_ids,
                                 response.user_content, context_cursor=cursor)

    def record_delivery(self, event: InboundEvent, request_id: str, *, outcome: str,
                        messages: list[str] | None = None, message_ids: list[str] | None = None,
                        error: str = "", started_at: float | None = None,
                        elapsed_ms: float | None = None) -> None:
        with self.connect() as conn:
            ended_at = time.time()
            row = conn.execute("INSERT INTO deliveries(request_id,session_key,event_key,outcome,message_ids,messages,error,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (request_id, event.session_key, event.key, outcome, encode(message_ids or []), encode(normalize_voice(messages or [])), error, ended_at))
            if started_at is not None and elapsed_ms is not None:
                conn.execute("INSERT INTO delivery_timings VALUES(?,?,?,?)", (row.lastrowid, started_at, ended_at, elapsed_ms))

    def add_request(self, event: InboundEvent, profile: Any, payload: dict[str, Any],
                    *, purpose: str = "chat", snapshot_revision: int = 0,
                    telemetry: dict[str, Any] | None = None, request_id: str = "") -> str:
        request_id = request_id or uuid.uuid4().hex
        pricing = {name: getattr(profile, name, None) for name in
                   ('currency', 'input_price_per_million', 'output_price_per_million',
                    'cache_read_price_per_million', 'cache_write_price_per_million')}
        pricing['version'] = getattr(profile, 'price_version', '') or hashlib.sha256(encode(pricing).encode()).hexdigest()[:16]
        frozen_telemetry = {**(telemetry or {}), 'pricing_snapshot': pricing}
        with self.connect() as conn:
            conn.execute("INSERT INTO requests(id,session_key,event_key,profile_id,provider,model,api_style,purpose,payload,outcome,started_at,snapshot_revision,telemetry) VALUES(?,?,?,?,?,?,?,?,?,'running',?,?,?)",
                         (request_id, event.session_key, event.key, profile.id, profile.provider, profile.model,
                          profile.api_style, purpose, encode(redact_payload(payload)), time.time(), snapshot_revision, encode(redact_payload(frozen_telemetry))))
        return request_id

    def finish_request(self, request_id: str, *, usage: dict[str, Any] | None = None,
                       outcome: str = "generated", error: str = "", messages: list[str] | None = None,
                       account: str = "unknown", diagnostics: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            telemetry_row = conn.execute("SELECT telemetry FROM requests WHERE id=?", (request_id,)).fetchone()
            telemetry = json.loads(telemetry_row[0]) if telemetry_row and telemetry_row[0] else {}
            measured = dict(usage or {})
            pricing = telemetry.get('pricing_snapshot')
            if pricing:
                measured.update(pricing_snapshot=pricing, currency=pricing.get('currency') or None)
            if diagnostics is not None:
                telemetry['model_diagnostics'] = redact_payload(diagnostics)
            conn.execute("UPDATE requests SET usage=?,outcome=?,error=?,ended_at=?,response=?,account=?,telemetry=? WHERE id=?",
                         (encode(redact_payload(measured)), outcome, error, time.time(), encode(messages or []), account,
                          encode(telemetry), request_id))

    def finish_interrupted_work(self) -> dict[str, int]:
        """Finalize this Harness's unfinished records before new workers start."""
        now, error = time.time(), "上次 Harness 运行被中断，结果未确认。"
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            jobs = [row[0] for row in conn.execute("SELECT id FROM background_jobs WHERE status='running'")]
            requests = conn.execute("UPDATE requests SET outcome='failed',error=?,ended_at=? WHERE outcome='running'",
                                    (error, now)).rowcount
            conn.execute("UPDATE background_jobs SET status='failed',result=?,updated_at=? WHERE status='running'",
                         (encode({'error': error}), now))
            conn.executemany("""INSERT INTO job_timings VALUES(?,NULL,?,'failed',?,0)
                ON CONFLICT(job_id) DO UPDATE SET ended_at=excluded.ended_at,state='failed',reason=excluded.reason""",
                [(job_id, now, error) for job_id in jobs])
        return {'requests': requests, 'jobs': len(jobs)}

    def request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            return redact_payload(decode_row(conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()))

    def requests(self, session_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clause = "WHERE session_key=?" if session_key else ""
            args = (session_key, limit) if session_key else (limit,)
            return [redact_payload(decode_row(row)) for row in conn.execute(f"SELECT * FROM requests {clause} ORDER BY started_at DESC LIMIT ?", args)]

    def previous_request(self, session_key: str, profile_id: str, *, exclude: str = "") -> dict[str, Any] | None:
        with self.connect() as conn:
            return redact_payload(decode_row(conn.execute("SELECT * FROM requests WHERE session_key=? AND profile_id=? AND id<>? ORDER BY started_at DESC LIMIT 1", (session_key, profile_id, exclude)).fetchone()))

    def snapshot(self, session_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            value = decode_row(conn.execute("SELECT * FROM snapshots WHERE session_key=? ORDER BY revision DESC LIMIT 1", (session_key,)).fetchone())
            if value:
                policy = conn.execute('SELECT value FROM settings WHERE key=?', (f"snapshot_filter:{session_key}:{value['revision']}",)).fetchone()
                policy = json.loads(policy['value']) if policy else None
                value['filter_users'] = policy.get('users') if isinstance(policy, dict) else policy
                value['excluded_turn_ids'] = policy.get('excluded_turn_ids', []) if isinstance(policy, dict) else []
            return value

    def snapshots(self, session_key: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [decode_row(row) for row in conn.execute("SELECT * FROM snapshots WHERE session_key=? ORDER BY revision", (session_key,))]

    def publish_snapshot(self, session_key: str, content: dict[str, Any], cutoff_turn_id: int, *, rebuild: bool = False,
                         filter_users: list[int] | None = None, excluded_turn_ids: list[int] | None = None) -> dict[str, Any]:
        filter_policy = {'users': sorted(filter_users), 'excluded_turn_ids': sorted(excluded_turn_ids if excluded_turn_ids is not None else self.excluded_turn_ids(session_key, set(filter_users)))} if filter_users is not None else None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT revision,cutoff_turn_id FROM snapshots WHERE session_key=? ORDER BY revision DESC LIMIT 1", (session_key,)).fetchone()
            if last and cutoff_turn_id <= last["cutoff_turn_id"] and not rebuild:
                raise ValueError("快照来源不能退回已发布的游标")
            revision = (last["revision"] if last else 0) + 1
            conn.execute("INSERT INTO snapshots(session_key,revision,cutoff_turn_id,content,created_at) VALUES(?,?,?,?,?)",
                         (session_key, revision, cutoff_turn_id, encode(content), time.time()))
            if filter_users is not None:
                conn.execute('INSERT INTO settings VALUES(?,?)', (f'snapshot_filter:{session_key}:{revision}', encode(filter_policy)))
        return self.snapshot(session_key)

    def add_tool_result(self, event: InboundEvent, call: ToolCall, result: ToolResult) -> int:
        with self.connect() as conn:
            row = conn.execute("INSERT INTO tools(session_key,event_key,name,arguments,result,created_at) VALUES(?,?,?,?,?,?)",
                         (event.session_key, event.key, call.name, encode(call.arguments), encode(result.to_dict()), time.time()))
        return int(row.lastrowid)

    def tool_results(self, session_key: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [decode_row(row) for row in conn.execute("SELECT * FROM tools WHERE session_key=? ORDER BY id DESC LIMIT ?", (session_key, limit))]

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def event_scopes(self, event_keys: list[str]) -> dict[str, bool]:
        with self.connect() as conn:
            return {row['event_key']: bool(row['allowed']) for row in conn.execute("""SELECT k.value AS event_key,
                COALESCE(json_extract(s.value,'$.chat_allowed'),1) AS allowed FROM json_each(?) k
                LEFT JOIN settings s ON s.key='event_scope:'||k.value""", (encode(event_keys),))}

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            if key.startswith('event_scope:'):
                previous = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                if (previous and json.loads(previous['value']).get('chat_allowed', True)
                        and not value.get('chat_allowed', True)):
                    event = conn.execute('SELECT session_key FROM events WHERE event_key=?',
                                         (key.removeprefix('event_scope:'),)).fetchone()
                    if event:
                        revision_key = 'cache_scope_revision:' + event['session_key']
                        old_revision = conn.execute('SELECT value FROM settings WHERE key=?',
                                                    (revision_key,)).fetchone()
                        revision = int(json.loads(old_revision['value'])) + 1 if old_revision else 1
                        conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) '
                                     'DO UPDATE SET value=excluded.value', (revision_key, encode(revision)))
            if key.startswith('growth_disabled:group:'):
                previous = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                before = set(json.loads(previous['value']) if previous else [])
                for entry in before.symmetric_difference(value):
                    edit_key = 'growth_hidden_native:' + key.removeprefix('growth_disabled:') + ':' + str(entry)
                    conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (edit_key, encode(True)))
            conn.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    def settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT * FROM settings")}

    get = get_setting
    set = set_setting

    def record_import(self, origin: str, source_hash: str, result: dict[str, Any]) -> bool:
        with self.connect() as conn:
            row = conn.execute("INSERT OR IGNORE INTO imports VALUES(?,?,?,?)", (origin, source_hash, encode(result), time.time()))
        return bool(row.rowcount)

    def imports(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [decode_row(row) for row in conn.execute("SELECT * FROM imports ORDER BY created_at")]

    def enqueue_job(self, kind: str, session_key: str, source: dict[str, Any]) -> str:
        source = normalize_voice(source)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT id,status,source FROM background_jobs WHERE kind=? AND session_key=? AND status='queued'", (kind, session_key)).fetchone()
            if existing:
                if kind in {"memory", "cognition", "growth"}:
                    merged = normalize_voice(json.loads(existing["source"]))
                    items = merged.get("events", [merged["event"]] if "event" in merged else [])
                    additions = source.get("events", [source["event"]] if "event" in source else [])
                    keys = {InboundEvent.from_dict(item).key for item in items}
                    for item in additions:
                        key = InboundEvent.from_dict(item).key
                        if key not in keys:
                            items.append(item)
                            keys.add(key)
                    merged["events"] = items
                    conn.execute("UPDATE background_jobs SET source=?,updated_at=? WHERE id=?", (encode(merged), time.time(), existing["id"]))
                elif kind in {"summary", "compaction"}:
                    conn.execute("UPDATE background_jobs SET source=?,updated_at=? WHERE id=?", (encode(source), time.time(), existing["id"]))
                return existing["id"]
            job_id, now = uuid.uuid4().hex, time.time()
            conn.execute("INSERT INTO background_jobs VALUES(?,?,?,?,?,?,?,?)", (job_id, kind, session_key, "queued", encode(source), "{}", now, now))
        return job_id

    def jobs(self, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.connect() as conn:
            clause = "WHERE status=?" if status else ""
            args = (status, limit, offset) if status else (limit, offset)
            return [decode_row(row) for row in conn.execute(f"SELECT * FROM background_jobs {clause} ORDER BY created_at,id LIMIT ? OFFSET ?", args)]

    def update_job(self, job_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        with self.connect() as conn:
            now = time.time()
            conn.execute("UPDATE background_jobs SET status=?,result=?,updated_at=? WHERE id=?", (status, encode(result or {}), now, job_id))
            if status == 'running':
                conn.execute("""INSERT INTO job_timings VALUES(?,?,NULL,?,'',1)
                    ON CONFLICT(job_id) DO UPDATE SET started_at=excluded.started_at,ended_at=NULL,
                    state=excluded.state,reason='',attempts=attempts+1""", (job_id, now, status))
            else:
                conn.execute("""INSERT INTO job_timings VALUES(?,NULL,?,?,?,0)
                    ON CONFLICT(job_id) DO UPDATE SET ended_at=excluded.ended_at,state=excluded.state,reason=excluded.reason""",
                    (job_id, now if status in {'completed', 'failed', 'queued'} else None, status,
                     (result or {}).get('error', '') if status == 'failed' else ('operator' if status == 'paused' else '')))

    def job_wait_reason(self, job_id: str, reason: str) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO job_timings VALUES(?,NULL,NULL,'queued',?,0)
                ON CONFLICT(job_id) DO UPDATE SET reason=excluded.reason""", (job_id, reason))

    def publish_summary(self, session_key: str, content: dict[str, Any], cutoff_event_id: int,
                        source_users: list[int], job_id: str) -> None:
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM settings WHERE key=?', ('group_state:' + session_key,)).fetchone()
            state = json.loads(row[0]) if row else {}
            revision = conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM summary_versions WHERE session_key=?', (session_key,)).fetchone()[0]
            published_at = time.time()
            state.update(summary=content, summary_cursor=cutoff_event_id, summary_source_users=source_users,
                         summary_revision=revision, summary_published_at=published_at)
            conn.execute('INSERT INTO summary_versions VALUES(?,?,?,?,?,?,?)',
                         (session_key, revision, encode(content), cutoff_event_id, encode(source_users), job_id, published_at))
            conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         ('group_state:' + session_key, encode(state)))

    add_job = enqueue_job

    def due_jobs(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        return self.jobs(status="queued", limit=limit, offset=offset)

    def put_asset(self, digest: str, media_type: str, raw: bytes) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?,?)", (digest, media_type, raw, time.time()))

    def asset(self, digest: str) -> tuple[str, bytes] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT media_type,bytes FROM assets WHERE digest=?", (digest,)).fetchone()
        return (row["media_type"], row["bytes"]) if row else None

    def memories(self, session_key: str, user_id: int, *, include_forgotten: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            condition = "" if include_forgotten else " AND status='active'"
            return [dict(row) for row in conn.execute("SELECT * FROM memory_records WHERE session_key=? AND user_id=?" + condition + " ORDER BY updated_at DESC", (session_key, user_id))]

    def import_legacy_fact(self, data: dict[str, Any], origin: str, table: str) -> dict[str, Any]:
        """Refresh a source-owned imported fact while preserving native corrections and forgetting."""
        source_id = data.get('id', data.get('fact_id'))
        user = int(data['user_id'])
        group = int(data.get('scope_group', data.get('group_id', 0)) or 0)
        session = f'group:{group}' if group else f'private:{user}'
        identity = f'{origin}:{table}:{source_id}'
        mapping_key = 'legacy_fact:' + hashlib.sha256(identity.encode()).hexdigest()
        event_key = 'import:' + identity
        content = str(data.get('content', data.get('fact', '')))
        quote = str(data.get('quote') or data.get('source_text') or content)
        kind = str(data.get('kind', 'fact'))
        state = 'active' if data.get('status', 'active') in {'active', 'approved'} else 'forgotten'
        digest = hashlib.sha256(encode(data).encode()).hexdigest()
        evidence = {key: data[key] for key in ('quote', 'evidence', 'evidence_ids', 'source_message_id',
            'source_hash', 'source_text', 'source_event_key') if key in data}
        state_hash = hashlib.sha256(encode([session, user, content, quote, kind, state, evidence]).encode()).hexdigest()
        changed_scopes = set()
        now = time.time()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            saved = conn.execute('SELECT value FROM settings WHERE key=?', (mapping_key,)).fetchone()
            mapping = json.loads(saved['value']) if saved else None
            record = conn.execute('SELECT * FROM memory_records WHERE id=?', (mapping['memory_id'],)).fetchone() if mapping else None
            if mapping:
                if record is None or record['version'] != mapping['imported_version']:
                    return {'status': 'preserved', 'memory_id': mapping['memory_id'], 'version': record['version'] if record else None}
                if mapping['source_hash'] == digest:
                    return {'status': 'unchanged', 'memory_id': record['id'], 'version': record['version']}
            else:
                # Adopt records produced by the first importer before this stable source mapping existed.
                record = conn.execute('SELECT * FROM memory_records WHERE event_key=? AND session_key=? AND user_id=? ORDER BY (content=?) DESC,updated_at DESC,id DESC LIMIT 1', (event_key, session, user, content)).fetchone()
                owned = bool(record and record['version'] == 1 and record['status'] == 'active')
                if record is None:
                    candidates = conn.execute('''SELECT DISTINCT m.* FROM memory_records m JOIN memory_versions v ON v.memory_id=m.id
                        WHERE m.session_key=? AND m.user_id=? AND v.version=1 AND v.content=?''', (session, user, content)).fetchall()
                    if len(candidates) == 1:
                        record = candidates[0]
                if record is not None:
                    mapping = {'memory_id': record['id'], 'imported_version': record['version'] if owned else 0}
                    if not owned:
                        mapping.update({'source_hash': digest, 'source_state_hash': state_hash, 'origin': origin, 'table': table, 'source_id': source_id})
                        conn.execute('INSERT INTO settings VALUES(?,?)', (mapping_key, encode(mapping)))
                        return {'status': 'preserved', 'memory_id': record['id'], 'version': record['version']}
                    duplicates = conn.execute("SELECT * FROM memory_records WHERE event_key=? AND id<>? AND status='active' AND version=1", (event_key, record['id'])).fetchall()
                    for duplicate in duplicates:
                        conn.execute("UPDATE memory_records SET status='forgotten',version=2,updated_at=? WHERE id=?", (now, duplicate['id']))
                        conn.execute("INSERT INTO memory_versions(memory_id,version,content,status,created_at) VALUES(?,2,?,'forgotten',?)", (duplicate['id'], duplicate['content'], now))
                        changed_scopes.add((duplicate['session_key'], duplicate['user_id']))
                elif not content.strip():
                    return {'status': 'empty', 'memory_id': None, 'version': None}
            if record is None:
                version = 1
                memory_id = conn.execute('''INSERT INTO memory_records(session_key,user_id,kind,content,quote,event_key,status,version,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (session, user, kind, content, quote, event_key, state, version, now, now)).lastrowid
                outcome = 'imported'
            else:
                memory_id, version = record['id'], record['version']
                previous_fields = [record[key] for key in ('session_key', 'user_id', 'content', 'quote', 'kind', 'status')]
                modified = previous_fields != [session, user, content, quote, kind, state]
                modified = modified or bool(mapping.get('source_state_hash') and mapping['source_state_hash'] != state_hash)
                if modified:
                    version += 1
                    conn.execute('''UPDATE memory_records SET session_key=?,user_id=?,kind=?,content=?,quote=?,event_key=?,status=?,version=?,updated_at=? WHERE id=?''',
                        (session, user, kind, content or record['content'], quote or record['quote'], event_key, state, version, now, memory_id))
                    changed_scopes.add((record['session_key'], record['user_id']))
                outcome = 'updated' if modified else 'unchanged'
            if outcome != 'unchanged':
                conn.execute('INSERT INTO memory_versions(memory_id,version,content,status,created_at) VALUES(?,?,?,?,?)', (memory_id, version, content or record['content'], state, now))
                changed_scopes.add((session, user))
            mapping = {'memory_id': memory_id, 'imported_version': version, 'source_hash': digest,
                       'source_state_hash': state_hash, 'origin': origin, 'table': table, 'source_id': source_id}
            conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (mapping_key, encode(mapping)))
            provenance = {'origin': origin, 'table': table, 'source_id': source_id, 'source_hash': digest, 'data': data}
            conn.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (f'legacy_fact_version:{memory_id}:{version}', encode(provenance)))
        for changed_session, changed_user in changed_scopes:
            self._sync_memory(changed_session, changed_user)
        return {'status': outcome, 'memory_id': memory_id, 'version': version}

    def memory_restrictions(self, session_key: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id,content,quote FROM memory_records WHERE session_key=? AND status='forgotten'", (session_key,)).fetchall()
            needles = {text for row in rows for text in (row['content'], row['quote']) if text}
            for row in rows:
                needles.update(item['content'] for item in conn.execute('SELECT content FROM memory_versions WHERE memory_id=?', (row['id'],)) if item['content'])
        return sorted(needles)

    def remember(self, session_key: str, user_id: int, content: str, *, event_key: str = "",
                 quote: str = "", kind: str = "fact", restore: bool = False) -> int | None:
        if not content.strip() or not quote:
            raise ValueError("记忆内容与原话证据不能为空")
        now = time.time()
        with self.connect() as conn:
            prior = conn.execute("SELECT * FROM memory_records WHERE session_key=? AND user_id=? AND content=?", (session_key, user_id, content)).fetchone()
            if prior and prior["status"] == "forgotten" and not restore:
                return None
            if prior and prior["status"] == "active" and prior["event_key"] == event_key and prior["quote"] == quote:
                return int(prior["id"])
            version = prior["version"] + 1 if prior else 1
            conn.execute("INSERT INTO memory_records(session_key,user_id,kind,content,quote,event_key,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?) ON CONFLICT(session_key,user_id,content) DO UPDATE SET quote=excluded.quote,event_key=excluded.event_key,status='active',version=excluded.version,updated_at=excluded.updated_at",
                         (session_key, user_id, kind, content, quote, event_key, version, now, now))
            row = conn.execute("SELECT id FROM memory_records WHERE session_key=? AND user_id=? AND content=?", (session_key, user_id, content)).fetchone()
            conn.execute("INSERT INTO memory_versions(memory_id,version,content,status,created_at) VALUES(?,?,?,'active',?)", (row["id"], version, content, now))
        self._sync_memory(session_key, user_id)
        return int(row["id"])

    def forget(self, session_key: str, user_id: int, needle: str = "") -> int:
        return self._memory_status(session_key, user_id, needle, "forgotten")

    def restore_memory(self, session_key: str, user_id: int, needle: str = "") -> int:
        return self._memory_status(session_key, user_id, needle, "active")

    def correct_memory(self, event: InboundEvent, record_id: int, content: str) -> None:
        if not content.strip() or content not in event.text:
            raise ValueError('新自述必须来自本轮本人原话')
        now = time.time()
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memory_records WHERE id=? AND session_key=? AND user_id=? AND status='active'", (record_id, event.session_key, event.user_id)).fetchone()
            if row is None:
                raise ValueError('当前会话没有该编号的本人有效记忆')
            version = row['version'] + 1
            conn.execute("UPDATE memory_records SET content=?,quote=?,event_key=?,version=?,updated_at=? WHERE id=?", (content, content, event.key, version, now, record_id))
            conn.execute("INSERT INTO memory_versions(memory_id,version,content,status,created_at) VALUES(?,?,?,'active',?)", (record_id, version, content, now))
        self._sync_memory(event.session_key, event.user_id)

    def _memory_status(self, session_key: str, user_id: int, needle: str, status: str) -> int:
        count = 0
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM memory_records WHERE session_key=? AND user_id=?", (session_key, user_id)).fetchall()
            for row in rows:
                if (not needle or needle in row["content"]) and row["status"] != status:
                    version = row["version"] + 1
                    conn.execute("UPDATE memory_records SET status=?,version=?,updated_at=? WHERE id=?", (status, version, time.time(), row["id"]))
                    conn.execute("INSERT INTO memory_versions(memory_id,version,content,status,created_at) VALUES(?,?,?,?,?)", (row["id"], version, row["content"], status, time.time()))
                    count += 1
        self._sync_memory(session_key, user_id)
        return count

    def _sync_memory(self, session_key: str, user_id: int) -> None:
        self.set_setting("memory:" + session_key + ":" + str(user_id), self.memories(session_key, user_id))

    def summary_events(self, session_key: str, after_id: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE session_key=? AND id>? ORDER BY id LIMIT ?", (session_key, after_id, limit)).fetchall()
        return [decode_row(row) for row in rows]

    def group_feature_enabled(self, group_id: int | None, feature_key: str) -> bool:
        """Read the same owned group switches used by business commands."""
        path = self.root / 'runtime' / 'business.db'
        if group_id is None or not path.is_file():
            return True
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, factory=_ClosingConnection) as conn:
            row = conn.execute(
                "SELECT f.configured_enabled FROM group_features f "
                "JOIN managed_groups g ON g.group_id=f.group_id "
                "WHERE f.group_id=? AND f.feature_key=? AND g.enabled=1",
                (group_id, feature_key)).fetchone()
        return bool(row and row[0])

    def group_present(self, group_id: int | None) -> bool:
        """False only for an owned group explicitly archived after bot leave.

        ChatService is also used in offline fixtures without a business DB or
        before first observation registers a group, so missing metadata is not
        treated as proof of absence.  The authoritative QQ synchronization is
        responsible for archiving known groups.
        """
        path = self.root / 'runtime' / 'business.db'
        if group_id is None or not path.is_file():
            return True
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, factory=_ClosingConnection) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'managed_groups' not in tables:
                return True
            row = conn.execute('SELECT enabled FROM managed_groups WHERE group_id=?', (int(group_id),)).fetchone()
        return row is None or bool(row[0])

    def blocked_users(self, group_id: int = 0) -> set[int]:
        path = self.root / 'runtime' / 'business.db'
        if not path.is_file():
            return set()
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, factory=_ClosingConnection) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            blocked = {int(row[0]) for table in ('active_filters', 'passive_filters') if table in tables
                       for row in conn.execute('SELECT user_id FROM ' + table)}
            if 'group_filters' in tables:
                blocked.update(int(row[0]) for row in conn.execute('SELECT user_id FROM group_filters WHERE group_id=?', (group_id,)))
        return blocked

    def delivered(self, request_id: str, event_key: str) -> bool:
        with self.connect() as conn:
            return bool(conn.execute("SELECT 1 FROM turns WHERE request_id=? AND event_key=? AND status='delivered'", (request_id, event_key)).fetchone())

    def background_usage(self, since: float) -> dict[str, int]:
        kinds = ('memory', 'cognition', 'summary', 'compaction', 'profile', 'profile_review', 'growth')
        with self.connect() as conn:
            rows = conn.execute("SELECT usage,telemetry FROM requests WHERE started_at>=? AND purpose IN (?,?,?,?,?,?,?)", (since, *kinds)).fetchall()
        tokens = 0
        for row in rows:
            usage, telemetry = json.loads(row['usage']), json.loads(row['telemetry'])
            total = usage.get('total_tokens')
            if total is None:
                total = telemetry.get('estimated_input_tokens', 0) + telemetry.get('reserved_output_tokens', 0)
            tokens += int(total)
        return {'requests': len(rows), 'tokens': tokens}

    def profile_versions(self, session_key: str, user_id: int) -> list[dict[str, Any]]:
        prefix = f'profile_version:{session_key}:{user_id}:'
        with self.connect() as conn:
            rows = conn.execute('SELECT value FROM settings WHERE substr(key,1,?)=?', (len(prefix), prefix)).fetchall()
        return sorted((json.loads(row['value']) for row in rows), key=lambda item: item['version'])

    def legacy_rows(self, table: str, user_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_rows'").fetchone():
                return []
            sql, args = 'SELECT * FROM legacy_rows WHERE table_name=?', [table]
            if user_id is not None:
                sql += " AND CAST(json_extract(data,'$.user_id') AS INTEGER)=?"
                args.append(user_id)
            rows = conn.execute(sql + ' ORDER BY imported_at DESC,row_key', args).fetchall()
        return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    def _legacy_memory_versions(self, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_rows'").fetchone():
                return []
            rows = conn.execute("""SELECT child.* FROM legacy_rows child JOIN legacy_rows parent
                ON parent.origin=child.origin AND parent.table_name='person_semantic_memory'
                AND json_extract(parent.data,'$.id')=json_extract(child.data,'$.memory_id')
                AND json_extract(parent.data,'$.version')=json_extract(child.data,'$.version')
                WHERE child.table_name='person_semantic_versions'
                AND CAST(json_extract(parent.data,'$.user_id') AS INTEGER)=?""", (user_id,)).fetchall()
        return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    def legacy_personal_context(self, session_key: str, user_id: int, *, persona: str = 'denia') -> dict[str, Any]:
        """Map copied legacy evidence to the new scope without reopening old databases."""
        group = int(session_key.split(':')[1]) if session_key.startswith('group:') else None
        memories, profiles, cognition = [], [], []
        restrictions = []
        versions = self._legacy_memory_versions(user_id)
        sources = self.legacy_rows('persona_sources', user_id)
        def allowed(row, data, *, source_key=''):
            if persona == 'denia' and 'tangtang/tangtang' in row['origin'].replace('\\', '/'):
                return False
            scope = data.get('scope_group', data.get('group_id', data.get('source_group')))
            if scope:
                return group == int(scope) or group is None
            if source_key:
                origin = next((item['data'] for item in sources if item['origin'] == row['origin'] and item['data'].get('event_key') == source_key), None)
                if origin:
                    return group == int(origin.get('group_id', 0)) or group is None
            return group is None
        def provenance(row):
            return {key: row[key] for key in ('origin', 'table_name', 'row_key', 'source_hash', 'imported_at')}
        for row in self.legacy_rows('person_restrictions', user_id):
            data = row['data']
            if data.get('active', 1) and allowed(row, data) and data.get('needle'):
                restrictions.append(data['needle'])
        for row in self.legacy_rows('person_semantic_memory', user_id):
            data = row['data']
            if data.get('status') not in {'active', 'approved'}:
                if data.get('content') and allowed(row, data):
                    restrictions.append(data['content'])
                continue
            evidence = next((item['data'] for item in versions if item['origin'] == row['origin']
                and item['data'].get('memory_id') == data.get('id') and item['data'].get('version') == data.get('version')), {})
            scoped = {**data, **({'source_group': evidence['source_group']} if evidence.get('source_group') else {})}
            # A global old record with a source group is recalled only in that group.
            if not data.get('scope_group') and evidence.get('source_group'):
                scoped['scope_group'] = evidence['source_group']
            if allowed(row, scoped) and not any(needle in str(data.get('content', '')) for needle in restrictions):
                memories.append({'id': data.get('id'), 'content': data.get('content', ''), 'category': data.get('category'),
                    'version': data.get('version'), 'evidence': evidence, 'source': provenance(row)})
        for row in self.legacy_rows('persona_portraits', user_id):
            data = row['data']
            if allowed(row, data):
                profiles.append({'content': data.get('content', ''), 'dependencies': data.get('dependencies'), 'source': provenance(row)})
        for table in ('persona_intents', 'persona_state_factors'):
            for row in self.legacy_rows(table, user_id):
                data = row['data']
                if allowed(row, data, source_key=data.get('source_key', '')):
                    cognition.append({'kind': 'intent' if table == 'persona_intents' else 'state',
                        'topic': data.get('topic'), 'content': data.get('description', data.get('label', '')),
                        'state': data.get('state'), 'version': data.get('version'), 'source': provenance(row)})
        return {'memory': memories[:20], 'profile': profiles[:1], 'cognition': cognition[:20], 'restrictions': restrictions}

    def impression(self, event: InboundEvent, trait: str, direction: int, quote: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO impression_events(session_key,user_id,event_key,trait,direction,quote,created_at) VALUES(?,?,?,?,?,?,?)", (event.session_key, event.user_id, event.key, trait, direction, quote, time.time()))

    def impressions(self, session_key: str, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM impression_events WHERE session_key=? AND user_id=? ORDER BY id DESC LIMIT 96", (session_key, user_id)).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            evidence = grouped.setdefault(row['trait'], [])
            if len(evidence) < 8:
                evidence.append(dict(row))
        return [{'trait': trait, 'score': sum(item['direction'] * .5 ** index for index, item in enumerate(items)) / sum(.5 ** index for index in range(len(items))), 'evidence': items} for trait, items in grouped.items()]

    def cognitive_update(self, event: InboundEvent, kind: str, topic: str, content: str, state: str, quote: str) -> int:
        now = time.time()
        with self.connect() as conn:
            prior = conn.execute("SELECT * FROM cognition_records WHERE session_key=? AND user_id=? AND kind=? AND topic=?", (event.session_key, event.user_id, kind, topic)).fetchone()
            if prior and prior['event_key'] == event.key and prior['content'] == content and prior['state'] == state:
                return int(prior['id'])
            version = prior['version'] + 1 if prior else 1
            conn.execute("INSERT INTO cognition_records(session_key,user_id,kind,topic,content,state,quote,event_key,version,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_key,user_id,kind,topic) DO UPDATE SET content=excluded.content,state=excluded.state,quote=excluded.quote,event_key=excluded.event_key,version=excluded.version,updated_at=excluded.updated_at", (event.session_key, event.user_id, kind, topic, content, state, quote, event.key, version, now))
            record = conn.execute("SELECT id FROM cognition_records WHERE session_key=? AND user_id=? AND kind=? AND topic=?", (event.session_key, event.user_id, kind, topic)).fetchone()
            conn.execute("INSERT INTO cognition_versions(record_id,version,content,state,quote,event_key,created_at) VALUES(?,?,?,?,?,?,?)", (record['id'], version, content, state, quote, event.key, now))
        return int(record['id'])

    def cognition(self, session_key: str, user_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM cognition_records WHERE session_key=? AND user_id=? ORDER BY updated_at DESC LIMIT 20", (session_key, user_id))]

    def grow(self, event: InboundEvent, content: str, quote: str, kind: str = 'expression', *, shared: bool = True) -> int:
        scope, now = ('global' if shared else event.session_key), time.time()
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO growth_records(scope,content,kind,status,version,event_key,quote,created_at,updated_at) VALUES(?,?,?,'active',1,?,?,?,?)", (scope, content, kind, event.key, quote, now, now))
            row = conn.execute("SELECT * FROM growth_records WHERE scope=? AND content=?", (scope, content)).fetchone()
            conn.execute("INSERT INTO growth_versions(growth_id,version,content,status,created_at) SELECT ?,1,?,?,? WHERE NOT EXISTS(SELECT 1 FROM growth_versions WHERE growth_id=?)", (row['id'], content, row['status'], now, row['id']))
        return int(row['id'])

    def growth(self, session_key: str = '', *, include_disabled: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            status = '' if include_disabled else " AND status='active'"
            rows = conn.execute("SELECT g.*,s.value AS provenance FROM growth_records g LEFT JOIN settings s ON s.key='growth_provenance:'||g.id WHERE scope IN ('global',?)" + status + " ORDER BY updated_at DESC", (session_key,)).fetchall()
        return [{**dict(row), 'provenance': json.loads(row['provenance']) if row['provenance'] else None} for row in rows]

    def import_legacy_growth(self, snapshot: Path) -> dict[str, int]:
        """Refresh source-owned growth and hidden settings while preserving native edits."""
        snapshot = Path(snapshot).resolve()
        with snapshot.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True, factory=_ClosingConnection) as source:
            source.row_factory = sqlite3.Row
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'growth' not in tables:
                return {'imported': 0, 'existing': 0, 'versions': 0}
            entries = [dict(row) for row in source.execute("SELECT * FROM growth WHERE persona='denia'")]
            versions = [dict(row) for row in source.execute("SELECT v.* FROM growth_versions v JOIN growth g ON g.id=v.entry_id WHERE g.persona='denia'")] if 'growth_versions' in tables else []
            evidence_ids = sorted({int(value) for row in versions for value in json.loads(row['evidence_ids'])})
            evidence = {row['id']: dict(row) for row in source.execute('SELECT * FROM evidence WHERE id IN (SELECT value FROM json_each(?))', (encode(evidence_ids),))} if 'evidence' in tables else {}
            hidden = [dict(row) for row in source.execute('SELECT * FROM growth_hidden')] if 'growth_hidden' in tables else []
        result = {'imported': 0, 'existing': 0, 'versions': 0}
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            def setting(key, default=None):
                row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                return json.loads(row['value']) if row else default
            def save(key, value):
                conn.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, encode(value)))
            for old in entries:
                mapping_key = 'legacy_growth_id:denia:' + str(old['id'])
                scope = 'global' if not old['group_id'] else 'group:' + str(old['group_id'])
                old_versions = sorted((row for row in versions if row['entry_id'] == old['id']), key=lambda row: row['version'])
                if not any(row['version'] == old['version'] for row in old_versions):
                    old_versions.append({'entry_id': old['id'], 'version': old['version'], 'content': old['content'], 'evidence_ids': '[]', 'created_at': old.get('created_at', 0)})
                current = next(row for row in old_versions if row['version'] == old['version'])
                now = current.get('created_at', time.time())
                state = 'active' if old['enabled'] else 'disabled'
                ids = {int(value) for row in old_versions for value in json.loads(row['evidence_ids'])}
                source_evidence = [evidence[key] for key in sorted(ids) if key in evidence]
                row_hash = hashlib.sha256(encode(old).encode()).hexdigest()
                source_hash = hashlib.sha256(encode([old, old_versions, source_evidence]).encode()).hexdigest()
                mapping = setting(mapping_key)
                record = conn.execute('SELECT * FROM growth_records WHERE id=?', (mapping['new_id'],)).fetchone() if mapping else None
                fresh = mapping is None
                if fresh:
                    same = conn.execute('SELECT * FROM growth_records WHERE scope=? AND content=?', (scope, old['content'])).fetchone()
                    if same:
                        record = same
                        mapping = {'new_id': same['id'], 'imported_native_version': 0}
                    else:
                        values = (scope, old['content'], old['topic'], state, old['version'], 'import:persona-growth:' + str(old['id']), '', now, now)
                        if not conn.execute('SELECT 1 FROM growth_records WHERE id=?', (old['id'],)).fetchone():
                            new_id = int(old['id'])
                            conn.execute('INSERT INTO growth_records VALUES(?,?,?,?,?,?,?,?,?,?)', (new_id, *values))
                        else:
                            new_id = conn.execute('INSERT INTO growth_records(scope,content,kind,status,version,event_key,quote,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', values).lastrowid
                        mapping = {'new_id': new_id, 'imported_native_version': old['version']}
                        record = conn.execute('SELECT * FROM growth_records WHERE id=?', (new_id,)).fetchone()
                new_id = mapping['new_id']
                provenance_key = 'growth_provenance:' + str(new_id)
                provenance = setting(provenance_key, {})
                if 'imported_native_version' not in mapping:
                    # Upgrade mappings written before row ownership was recorded.
                    prior_versions = [int(value) for value in provenance.get('version_evidence', {})]
                    mapping['imported_native_version'] = max(prior_versions, default=old['version']) if record and record['event_key'] == 'import:persona-growth:' + str(old['id']) else 0
                owned = bool(record and record['version'] == mapping['imported_native_version'])
                if owned and (fresh or mapping.get('source_hash') != source_hash):
                    native_versions = provenance.get('native_versions', {})
                    if not native_versions and not fresh:
                        for prior in conn.execute('SELECT * FROM growth_versions WHERE growth_id=?', (new_id,)):
                            legacy_ids = provenance.get('version_evidence', {}).get(str(prior['version']), [])
                            prior_evidence = [item for item in provenance.get('evidence', []) if item['id'] in legacy_ids]
                            native_versions[str(prior['version'])] = {'legacy_version': prior['version'], 'content': prior['content'], 'evidence_ids': legacy_ids, 'evidence': prior_evidence}
                    def version_hash(value):
                        return hashlib.sha256(encode([value['legacy_version'], value['content'], value['evidence_ids'], value['evidence']]).encode()).hexdigest()
                    known = {version_hash(value) for value in native_versions.values()}
                    native_version = record['version']
                    last_content, last_state = record['content'], record['status']
                    for row in old_versions:
                        legacy_ids = json.loads(row['evidence_ids'])
                        version_data = {'legacy_version': row['version'], 'content': row['content'], 'evidence_ids': legacy_ids,
                                        'evidence': [evidence[key] for key in legacy_ids if key in evidence]}
                        fingerprint = version_hash(version_data)
                        if fingerprint in known:
                            continue
                        native_version = row['version'] if fresh else native_version + 1
                        version_state = state if row['version'] == old['version'] else 'active'
                        conn.execute('INSERT INTO growth_versions(growth_id,version,content,status,created_at) VALUES(?,?,?,?,?)', (new_id, native_version, row['content'], version_state, row['created_at']))
                        native_versions[str(native_version)] = version_data
                        last_content, last_state = row['content'], version_state
                        known.add(fingerprint)
                        result['versions'] += 1
                    if (last_content, last_state) != (old['content'], state):
                        native_version += 1
                        conn.execute('INSERT INTO growth_versions(growth_id,version,content,status,created_at) VALUES(?,?,?,?,?)', (new_id, native_version, old['content'], state, time.time()))
                        native_versions[str(native_version)] = {**native_versions.get(str(native_version - 1), {}),
                            'legacy_version': old['version'], 'content': old['content'], 'evidence_ids': json.loads(current['evidence_ids']),
                            'evidence': [evidence[key] for key in json.loads(current['evidence_ids']) if key in evidence]}
                        result['versions'] += 1
                    conn.execute('UPDATE growth_records SET scope=?,content=?,kind=?,status=?,version=?,updated_at=? WHERE id=?', (scope, old['content'], old['topic'], state, native_version, time.time(), new_id))
                    mapping.update({'imported_native_version': native_version, 'source_hash': source_hash, 'source_row_hash': row_hash})
                    provenance.update({'legacy_id': old['id'], 'persona': 'denia', 'topic': old['topic'], 'source_hash': source_hash,
                        'snapshot_hash': digest, 'evidence': source_evidence, 'native_versions': native_versions,
                        'version_evidence': {str(row['version']): json.loads(row['evidence_ids']) for row in old_versions}})
                    save(provenance_key, provenance)
                source_hidden = {row['group_id'] for row in hidden if row['entry_id'] == old['id']}
                prior_hidden = mapping.get('hidden_applied', {})
                current_groups = {int(row['key'].removeprefix('growth_disabled:group:')) for row in conn.execute("SELECT key,value FROM settings WHERE key LIKE 'growth_disabled:group:%'") if new_id in json.loads(row['value'])}
                groups = source_hidden | current_groups | {int(group) for group in prior_hidden}
                for group in groups:
                    key = 'growth_disabled:group:' + str(group)
                    disabled = set(setting(key, []))
                    previous = prior_hidden.get(str(group), new_id in disabled if not fresh else False)
                    current_hidden = new_id in disabled
                    native_edit = setting(f'growth_hidden_native:group:{group}:{new_id}', False)
                    if native_edit or current_hidden != previous:
                        continue
                    if group in source_hidden:
                        disabled.add(new_id)
                    else:
                        disabled.discard(new_id)
                    save(key, sorted(disabled))
                    prior_hidden[str(group)] = group in source_hidden
                mapping['hidden_applied'] = prior_hidden
                save(mapping_key, mapping)
                result['imported' if fresh and owned else 'existing'] += 1
            conn.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?,?)', ('legacy_growth:denia:' + digest, digest, encode(result), time.time()))
        return result

    def change_growth(self, record_id: int, *, status: str | None = None, rollback_version: int | None = None) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM growth_records WHERE id=?", (record_id,)).fetchone()
            if row is None:
                raise ValueError('成长条目不存在')
            content = row['content']
            if rollback_version is not None:
                old = conn.execute("SELECT * FROM growth_versions WHERE growth_id=? AND version=?", (record_id, rollback_version)).fetchone()
                if old is None:
                    raise ValueError('成长版本不存在')
                content, status = old['content'], old['status']
            status = status or row['status']
            version, now = row['version'] + 1, time.time()
            conn.execute("UPDATE growth_records SET content=?,status=?,version=?,updated_at=? WHERE id=?", (content, status, version, now, record_id))
            conn.execute("INSERT INTO growth_versions(growth_id,version,content,status,created_at) VALUES(?,?,?,?,?)", (record_id, version, content, status, now))
        return {'id': record_id, 'version': version, 'content': content, 'status': status}
