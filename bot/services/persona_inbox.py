"""Durable receive-time observations; no model or platform I/O on capture."""
from __future__ import annotations

import time
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_observation_inbox(
 id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
 persona TEXT NOT NULL, route_version TEXT NOT NULL,
 group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, message_id TEXT NOT NULL,
 text TEXT NOT NULL, occurred_at REAL NOT NULL, received_at REAL NOT NULL,
 attribution TEXT NOT NULL DEFAULT 'ambient', revision INTEGER NOT NULL DEFAULT 1,
 state TEXT NOT NULL DEFAULT 'pending', owner TEXT NOT NULL DEFAULT '',
 epoch INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', completed_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_observation_ready
 ON persona_observation_inbox(state,next_attempt,received_at);
CREATE INDEX IF NOT EXISTS idx_observation_person
 ON persona_observation_inbox(persona,user_id,state,id);
CREATE TABLE IF NOT EXISTS persona_observation_service(
 persona TEXT NOT NULL,user_id INTEGER NOT NULL,last_served REAL NOT NULL,
 PRIMARY KEY(persona,user_id));
CREATE TABLE IF NOT EXISTS persona_observation_replies(
 event_key TEXT PRIMARY KEY, message_id TEXT NOT NULL);
"""


def capture(conn, *, persona: str, route_version: str, group_id: int,
            user_id: int, message_id: str, text: str, occurred_at: float,
            received_at: float, attribution: str = 'ambient', reply_to: str = '') -> None:
    """Called inside the same transaction as the source group message INSERT."""
    if not message_id or not text or user_id <= 0:
        return
    conn.execute("""INSERT INTO persona_observation_inbox
        (event_key,persona,route_version,group_id,user_id,message_id,text,
         occurred_at,received_at,attribution) VALUES(?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(event_key) DO NOTHING""",
        (f'{group_id}:{message_id}', persona, route_version, group_id, user_id,
         message_id, text[:16000], occurred_at, received_at, attribution))
    if reply_to:
        conn.execute('INSERT OR IGNORE INTO persona_observation_replies VALUES(?,?)',
                     (f'{group_id}:{message_id}', str(reply_to)))


class ObservationInbox:
    def __init__(self, db):
        self.db = db

    def sources(self, group_id: int, message_ids, persona: str, *, direct=False, owner='') -> list[dict]:
        keys = tuple(f'{group_id}:{m}' for m in message_ids)
        if not keys:
            return []
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if direct:
                conn.executemany("""UPDATE persona_observation_inbox SET
                    attribution='direct',revision=revision+1,state='pending',
                    owner='',lease_until=0,next_attempt=0
                    WHERE event_key=? AND persona=? AND attribution='ambient'""",
                    [(key, persona) for key in keys])
            if owner:
                conn.executemany("""UPDATE persona_observation_inbox SET state='processing',
                    owner=?,epoch=epoch+1,lease_until=? WHERE event_key=? AND persona=?
                    AND state IN ('pending','retry')""",
                    [(owner, time.time() + 300, key, persona) for key in keys])
            return [dict(r) for r in conn.execute(
                f"SELECT *, (SELECT message_id FROM persona_observation_replies r WHERE r.event_key=q.event_key) AS reply_to FROM persona_observation_inbox q WHERE event_key IN ({','.join('?' for _ in keys)}) AND persona=? ORDER BY id",
                (*keys, persona))]

    def release(self, rows, owner) -> None:
        with self.db._connect() as conn:
            conn.executemany("""UPDATE persona_observation_inbox SET state='pending',owner='',lease_until=0
                WHERE id=? AND revision=? AND owner=? AND state='processing'""",
                [(r['id'], r['revision'], owner) for r in rows])

    def claim(self, *, now: float | None = None, limit=12, lease=90, active_groups=()) -> list[dict]:
        now = time.time() if now is None else now
        owner = uuid.uuid4().hex
        if not active_groups:
            return []
        groups = tuple(active_groups)
        with self.db._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("""UPDATE persona_observation_inbox SET state='retry',owner=''
                WHERE state='processing' AND lease_until<=?""", (now,))
            head = conn.execute(f"""SELECT * FROM persona_observation_inbox AS q
                WHERE persona='denia' AND state IN ('pending','retry') AND next_attempt<=?
                AND group_id IN ({','.join('?' for _ in groups)})
                AND received_at<=? AND NOT EXISTS(SELECT 1 FROM persona_observation_inbox p
                    WHERE p.persona=q.persona AND p.user_id=q.user_id AND p.state='processing')
                ORDER BY COALESCE((SELECT last_served FROM persona_observation_service s
                    WHERE s.persona=q.persona AND s.user_id=q.user_id),0),received_at,id LIMIT 1""", (now, *groups, now - 2)).fetchone()
            if not head:
                return []
            conn.execute('INSERT OR REPLACE INTO persona_observation_service VALUES(?,?,?)',
                         (head['persona'], head['user_id'], now))
            rows = conn.execute(f"""SELECT *, (SELECT message_id FROM persona_observation_replies r WHERE r.event_key=q.event_key) AS reply_to FROM persona_observation_inbox q
                WHERE persona=? AND user_id=? AND state IN ('pending','retry') AND next_attempt<=?
                AND group_id IN ({','.join('?' for _ in groups)}) ORDER BY id LIMIT ?""",
                (head['persona'], head['user_id'], now, *groups, min(12, max(1, limit)))).fetchall()
            selected, chars = [], 0
            for row in rows:
                if selected and chars + len(row['text']) > 12000:
                    break
                chars += len(row['text'])
                selected.append(dict(row))
                conn.execute("""UPDATE persona_observation_inbox SET state='processing',
                    owner=?,epoch=epoch+1,lease_until=?,attempts=attempts+1 WHERE id=?""",
                    (owner, now + lease, row['id']))
                selected[-1].update(owner=owner, epoch=row['epoch'] + 1, attempts=row['attempts'] + 1)
            return selected

    def acknowledge(self, rows, *, now=None, error='', permanent=False) -> None:
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            for row in rows:
                attempts = row.get('attempts', 0)
                state = ('quarantined' if permanent or attempts >= 6 else 'retry') if error else 'applied'
                delay = min(3600, 5 * 2 ** min(attempts, 10)) if error else 0
                # A direct-attribution upgrade invalidates an old ambient lease.
                conn.execute("""UPDATE persona_observation_inbox SET state=?,error=?,
                    next_attempt=?,completed_at=?,owner='',lease_until=0
                    WHERE id=? AND revision=? AND owner=? AND epoch=?""",
                    (state, error[:80], now + delay, now if not error else 0,
                     row['id'], row['revision'], row.get('owner', ''), row.get('epoch', 0)))

    def replay_quarantined(self) -> int:
        with self.db._connect() as conn:
            return conn.execute("""UPDATE persona_observation_inbox SET state='pending',
                attempts=0,next_attempt=0,error='' WHERE state='quarantined'""").rowcount

    def recover(self) -> int:
        """Startup only: no previous process can own a live local lease."""
        with self.db._connect() as conn:
            return conn.execute("""UPDATE persona_observation_inbox SET state='retry',
                owner='',epoch=epoch+1,lease_until=0 WHERE state='processing'""").rowcount

    def diagnostics(self, now=None) -> dict:
        now = time.time() if now is None else now
        with self.db._connect() as conn:
            counts = dict(conn.execute('SELECT state,count(*) FROM persona_observation_inbox GROUP BY state'))
            row = conn.execute("""SELECT min(received_at),count(DISTINCT user_id),sum(length(text))
                FROM persona_observation_inbox WHERE state<>'applied'""").fetchone()
        return {'states': counts, 'oldest_seconds': max(0, now - row[0]) if row[0] else 0,
                'pending_users': row[1], 'protected_characters': row[2] or 0}
