"""Durable person-scoped profile work, evidence coverage and atomic publication."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from bot.services.persona_profile_contract import ProfileBatch, apply_review, validate_draft
from bot.services.persona_memory_store import normalize


SCHEMA = '''
CREATE TABLE IF NOT EXISTS persona_profile_jobs(
 user_id INTEGER PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
 generation INTEGER NOT NULL DEFAULT 0, owner TEXT NOT NULL DEFAULT '',
 lease_until REAL NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, last_served REAL NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL, error TEXT NOT NULL DEFAULT '', priority INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS persona_profile_scheduler(id INTEGER PRIMARY KEY CHECK(id=1), turn INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS persona_profile_seen(
 event_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, generation INTEGER NOT NULL, covered_chars INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS persona_profile_versions(
 user_id INTEGER NOT NULL, generation INTEGER NOT NULL, content TEXT NOT NULL,
 source_versions TEXT NOT NULL, review TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(user_id,generation));
CREATE TABLE IF NOT EXISTS persona_profile_approved(
 memory_id INTEGER PRIMARY KEY, version INTEGER NOT NULL, scope TEXT NOT NULL,
 basis TEXT NOT NULL, generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS persona_profile_migrations(version TEXT PRIMARY KEY, completed_at REAL NOT NULL);
'''


def ensure_profile_schema(conn):
    conn.executescript(SCHEMA)
    if 'priority' not in {r[1] for r in conn.execute('PRAGMA table_info(persona_profile_jobs)')}:
        conn.execute('BEGIN IMMEDIATE')
        if 'priority' not in {r[1] for r in conn.execute('PRAGMA table_info(persona_profile_jobs)')}:
            conn.execute('ALTER TABLE persona_profile_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
        conn.commit()


def enqueue(conn, user_id, now, priority=0):
    if user_id > 0:
        conn.execute('''INSERT INTO persona_profile_jobs(user_id,updated_at,priority) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET updated_at=excluded.updated_at,
            priority=max(priority,excluded.priority),
            state=CASE WHEN state='complete' THEN 'pending' ELSE state END''', (user_id, now, priority))


class ProfileStore:
    def __init__(self, cognition):
        self.cognition = cognition

    def recover(self):
        with self.cognition.connect() as conn:
            conn.execute("UPDATE persona_profile_jobs SET state='pending',owner='',lease_until=0 WHERE state='processing'")

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO persona_profile_scheduler VALUES(1,0)')
            turn = conn.execute('SELECT turn FROM persona_profile_scheduler WHERE id=1').fetchone()[0]
            order = 'priority DESC,last_served,user_id' if turn % 3 < 2 else 'last_served,user_id'
            job = conn.execute('''SELECT * FROM persona_profile_jobs WHERE
                (state IN ('pending','retry') OR (state='processing' AND lease_until<?))
                AND next_attempt<=? ORDER BY ''' + order + ' LIMIT 1', (now, now)).fetchone()
            if not job:
                return None
            conn.execute('UPDATE persona_profile_scheduler SET turn=turn+1 WHERE id=1')
            owner = uuid.uuid4().hex
            conn.execute("UPDATE persona_profile_jobs SET state='processing',owner=?,lease_until=?,last_served=? WHERE user_id=?",
                         (owner, now + 150, now, job['user_id']))
            previous_row = conn.execute('SELECT content FROM persona_profile_versions WHERE user_id=? AND generation=?',
                                        (job['user_id'], job['generation'])).fetchone()
            previous = json.loads(previous_row['content']) if previous_row else {'observations': [], 'portrait': []}
            # Newest evidence establishes/corrects a first impression quickly;
            # oldest uncovered evidence guarantees eventual historical coverage.
            unseen = '''SELECT s.*,CASE WHEN p.revision=s.revision THEN p.covered_chars ELSE 0 END AS covered_chars
                FROM persona_sources s LEFT JOIN persona_profile_seen p ON p.event_key=s.event_key
                WHERE s.user_id=? AND (p.event_key IS NULL OR p.revision<>s.revision OR p.covered_chars<length(s.text))'''
            rows = [dict(r) for r in conn.execute(unseen + ' ORDER BY s.occurred_at DESC LIMIT 16', (job['user_id'],))]
            rows += [dict(r) for r in conn.execute(unseen + ' ORDER BY s.occurred_at,s.event_key LIMIT 32', (job['user_id'],))]
            previous_keys = {e['event_key'] for i in previous['observations'] for e in i['evidence']}
            present = {r['event_key'] for r in rows}
            for key in previous_keys:
                if key in present:
                    continue
                row = conn.execute('SELECT * FROM persona_sources WHERE event_key=? AND user_id=?', (key, job['user_id'])).fetchone()
                if row:
                    rows.append({**dict(row), 'covered_chars': len(row['text'])})
            sources = list({r['event_key']: r for r in rows}.values())
            # Raw text is bounded; quoted prior evidence remains represented.
            for row in sources:
                offset = row.pop('covered_chars', 0)
                row['reviewed_chars'] = min(len(row['text']), offset + 1500)
                if len(row['text']) > 1500:
                    quotes = [e['quote'] for i in previous['observations'] for e in i['evidence'] if e['event_key'] == row['event_key']]
                    row['text'] = row['text'][max(0, offset-200):row['reviewed_chars']] + ('\n[同条发言的历史引用]\n' + '\n'.join(quotes) if quotes else '')
            total = conn.execute('SELECT count(*) FROM persona_sources WHERE user_id=?', (job['user_id'],)).fetchone()[0]
        return ProfileBatch(job['user_id'], owner, job['generation'], sources, previous, total, job['error'])

    def fail(self, batch, error, now=None):
        now = time.time() if now is None else now
        with self.cognition.connect() as conn:
            row = conn.execute('SELECT attempts FROM persona_profile_jobs WHERE user_id=? AND owner=?', (batch.user_id, batch.owner)).fetchone()
            if row:
                attempts = row['attempts'] + 1
                delay = min(300, 10 * 2 ** min(attempts - 1, 5))
                conn.execute("""UPDATE persona_profile_jobs SET state='retry',attempts=?,next_attempt=?,error=?,
                    owner='',lease_until=0 WHERE user_id=? AND owner=?""",
                    (attempts, now + delay, error[:80], batch.user_id, batch.owner))

    def publish(self, batch, draft, review, now=None):
        now = time.time() if now is None else now
        content = apply_review(validate_draft(draft, batch), review, batch)
        generation = batch.generation + 1
        source_versions = {s['event_key']: s['revision'] for s in batch.sources}
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = conn.execute('SELECT * FROM persona_profile_jobs WHERE user_id=?', (batch.user_id,)).fetchone()
            if not job or job['owner'] != batch.owner or job['generation'] != batch.generation or job['state'] != 'processing':
                raise ValueError('profile_lease_changed')
            for key, revision in source_versions.items():
                current = conn.execute('SELECT user_id,revision,text FROM persona_sources WHERE event_key=?', (key,)).fetchone()
                if not current or current['user_id'] != batch.user_id or current['revision'] != revision:
                    raise ValueError('profile_source_changed')
                if any(ref['quote'] not in current['text'] for item in content['observations']
                       for ref in item['evidence'] if ref['event_key'] == key):
                    raise ValueError('profile_quote_not_in_original')
            # Invalidate the previous projection, retaining every old claim,
            # evidence and version. Whole-profile regeneration reconciles topics.
            conn.execute("""UPDATE person_semantic_memory SET status='superseded' WHERE user_id=?
                AND status IN ('active','candidate','review') AND id IN
                (SELECT memory_id FROM persona_claim_metadata WHERE kind='impression')""", (batch.user_id,))
            ids = [self._publish_item(conn, batch, item, generation, now) for item in content['observations']]
            conn.execute('INSERT INTO persona_profile_versions VALUES(?,?,?,?,?,?)',
                         (batch.user_id, generation, json.dumps(content, ensure_ascii=False),
                          json.dumps(source_versions), json.dumps(review, ensure_ascii=False), now))
            conn.executemany('INSERT OR REPLACE INTO persona_profile_seen VALUES(?,?,?,?)',
                             [(s['event_key'], s['revision'], generation, s['reviewed_chars']) for s in batch.sources])
            pending = conn.execute('''SELECT 1 FROM persona_sources s LEFT JOIN persona_profile_seen p ON p.event_key=s.event_key
                WHERE s.user_id=? AND (p.event_key IS NULL OR p.revision<>s.revision OR p.covered_chars<length(s.text)) LIMIT 1''', (batch.user_id,)).fetchone()
            conn.execute("""UPDATE persona_profile_jobs SET state=?,generation=?,owner='',lease_until=0,
                attempts=0,next_attempt=0,error='',priority=CASE WHEN ? THEN priority ELSE 0 END WHERE user_id=?""",
                ('pending' if pending else 'complete', generation, bool(pending), batch.user_id))
            portrait = '\n'.join(s['text'] for s in content['portrait']) or '\n'.join(i['statement'] for i in content['observations'])
            conn.execute('INSERT OR REPLACE INTO persona_portraits VALUES(?,?,?,?)',
                         (batch.user_id, portrait, json.dumps(ids), now))
        return content

    @staticmethod
    def _publish_item(conn, batch, item, generation, now):
        stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
        category = 'profile:impression'
        normalized = normalize(item['statement'])
        old = conn.execute('SELECT id,version FROM person_semantic_memory WHERE user_id=? AND scope_group=0 AND category=? AND normalized=?',
                           (batch.user_id, category, normalized)).fetchone()
        version = old['version'] + 1 if old else 1
        if old:
            mid = old['id']
            conn.execute("UPDATE person_semantic_memory SET content=?,status='active',version=?,updated_at=? WHERE id=?",
                         (item['statement'], version, stamp, mid))
        else:
            mid = conn.execute("""INSERT INTO person_semantic_memory
                (user_id,scope_group,category,content,normalized,tags,status,version,created_at,updated_at)
                VALUES(?,0,?,?,?,'[]','active',1,?,?)""", (batch.user_id, category, item['statement'], normalized, stamp, stamp)).lastrowid
        catalog = {s['event_key']: s for s in batch.sources}
        source = catalog[item['evidence'][0]['event_key']]
        direct = any(catalog[e['event_key']]['attribution'] == 'direct' for e in item['evidence'])
        conn.execute('INSERT OR REPLACE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,0)',
                     (mid, 'impression', item['statement'][:80], item['context'], 'inference', .6 if direct else .3,
                      max(catalog[e['event_key']]['occurred_at'] for e in item['evidence']), 'direct' if direct else 'ambient'))
        conn.execute('INSERT INTO person_semantic_versions VALUES(?,?,?,?,?,?,?,?,?)',
                     (mid, version, item['statement'], item['evidence'][0]['quote'], '[]', source['group_id'], source['message_id'], 'profile_review', stamp))
        for ref in item['evidence']:
            conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)', (mid, version, ref['event_key'], ref['quote'], 'supports'))
        conn.execute('INSERT OR REPLACE INTO persona_profile_approved VALUES(?,?,?,?,?)', (mid, version, item['scope'], item['basis'], generation))
        return mid

    def display(self, user_id):
        with self.cognition.connect() as conn:
            job = conn.execute('SELECT * FROM persona_profile_jobs WHERE user_id=?', (user_id,)).fetchone()
            row = conn.execute('SELECT content FROM persona_profile_versions WHERE user_id=? ORDER BY generation DESC LIMIT 1', (user_id,)).fetchone()
            if not row:
                return '正在根据你本人的发言整理初步认识。' if job else '目前还没有足够的本人发言来形成具体认识。'
            content = json.loads(row['content'])
            restrictions = [r[0] for r in conn.execute('SELECT needle FROM person_restrictions WHERE user_id=? AND active=1', (user_id,))]
        allowed = {i for i, o in enumerate(content['observations']) if not any(n in normalize(o['statement']) or
            any(n in normalize(e['quote']) for e in o['evidence']) for n in restrictions)}
        sentences = [s['text'] for s in content['portrait'] if all(i in allowed for i in s['observations'])
                     and not any(n in normalize(s['text']) for n in restrictions)]
        if not sentences:
            sentences = [o['statement'] for i, o in enumerate(content['observations']) if i in allowed]
        if not sentences:
            return '已经看过你的发言，但目前还不足以对你作具体判断。之后的交流会继续更新。'
        lines = ['目前对你的认识（会随新交流修订）：', *sentences]
        quotes = list(dict.fromkeys(e['quote'] for i, o in enumerate(content['observations']) if i in allowed for e in o['evidence']))
        lines += ['依据：' + q[:160] for q in quotes[:3]]
        if job and job['state'] != 'complete':
            lines.append('新的发言或更早的记录还在整理中。')
        return '\n'.join(lines)
