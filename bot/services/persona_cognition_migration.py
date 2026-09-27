"""Idempotent adoption of legacy records; never erase original evidence/history."""
from __future__ import annotations

import json
import time
from datetime import datetime

from bot.services.persona_impressions import SCHEMA as IMPRESSION_SCHEMA, PersonalImpressions
from bot.services.persona_memory_store import normalize


def timestamp(value) -> float:
    try:
        return float(value)
    except (ValueError, TypeError):
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except ValueError:
            return 0


def migrate_legacy(cognition, central=None) -> dict:
    """Called on a snapshot for dry-run, or while the live writer is stopped."""
    now = time.time()
    impressions = PersonalImpressions(cognition.people)
    with cognition.connect() as conn:
        conn.executescript(IMPRESSION_SCHEMA)
        users = [r[0] for r in conn.execute('SELECT DISTINCT user_id FROM person_impression_events')]
    old_impressions = {uid: impressions.recall(uid) for uid in users}
    counts = {'semantic': 0, 'facts': 0, 'impressions': 0, 'growth': 0, 'relationships': 0, 'episodes': 0}
    with cognition.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute('SELECT * FROM person_semantic_memory WHERE id NOT IN (SELECT memory_id FROM persona_claim_metadata)').fetchall():
            conn.execute('INSERT INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                         (row['id'], 'fact', row['category'], '', 'self_report', .5, timestamp(row['updated_at']), 'legacy', 0))
            counts['semantic'] += 1
            for ref in conn.execute('SELECT * FROM person_semantic_evidence WHERE memory_id=?', (row['id'],)).fetchall():
                _legacy_source(conn, ref['event_key'], row['user_id'], ref['source_group'], ref['quote'], ref['created_at'])
                version = conn.execute('SELECT version FROM person_semantic_versions WHERE memory_id=? AND quote=? ORDER BY version DESC LIMIT 1',
                                       (row['id'], ref['quote'])).fetchone()
                if version:
                    conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)',
                                 (row['id'], version[0], ref['event_key'], ref['quote'], 'supports'))
        for row in conn.execute('SELECT * FROM person_facts').fetchall():
            key = 'fact:' + str(row['id'])
            if conn.execute('SELECT 1 FROM persona_migration_map WHERE source=?', (key,)).fetchone():
                continue
            mid = _adopt(conn, row['user_id'], 'fact', row['kind'], row['content'], row['status'], row['updated_at'],
                         scope_group=int(row['scope_group'] or 0))
            conn.execute('INSERT OR IGNORE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                         (mid, 'fact', row['kind'], '', 'self_report', row['confidence'], timestamp(row['updated_at']), 'legacy', 0))
            conn.execute('INSERT INTO persona_migration_map VALUES(?,?,?)', (key, mid, now))
            for evidence in conn.execute('SELECT event_key FROM person_fact_evidence WHERE fact_id=?', (row['id'],)):
                conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)',
                             (mid, 1, evidence['event_key'], row['content'], 'supports'))
            counts['facts'] += 1
        for uid, rows in old_impressions.items():
            for row in rows:
                key = f"impression:{uid}:{row['trait']}"
                if conn.execute('SELECT 1 FROM persona_migration_map WHERE source=?', (key,)).fetchone():
                    continue
                mid = _adopt(conn, uid, 'impression', row['trait'], row['text'], 'active', row['updated_at'])
                conn.execute('INSERT OR IGNORE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                             (mid, 'impression', row['trait'], '旧版交流观察，待新证据修订', 'inference', .4,
                              timestamp(row['updated_at']), 'legacy', 0))
                conn.execute('INSERT INTO persona_migration_map VALUES(?,?,?)', (key, mid, now))
                evidence_rows = conn.execute('SELECT * FROM person_impression_events WHERE user_id=? AND trait=?', (uid, row['trait'])).fetchall()
                for evidence in evidence_rows:
                    _legacy_source(conn, evidence['event_key'], uid, evidence['source_group'], evidence['quote'], evidence['created_at'])
                    conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)',
                                 (mid, 1, evidence['event_key'], evidence['quote'], 'supports' if evidence['direction'] > 0 else 'opposes'))
                counts['impressions'] += 1
        for relation in conn.execute('SELECT * FROM person_relations WHERE interaction_count>0').fetchall():
            key = 'relationship:' + str(relation['user_id'])
            if conn.execute('SELECT 1 FROM persona_migration_map WHERE source=?', (key,)).fetchone():
                continue
            content = f"旧版记录中已经有{relation['interaction_count']}次交流，不应当作首次见面；具体关系仍依实际证据判断。"
            mid = _adopt(conn, relation['user_id'], 'relationship', '既往交流', content, 'active', relation['updated_at'])
            conn.execute('INSERT OR IGNORE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                         (mid, 'relationship', '既往交流', '仅表示此前互动，不代表亲密承诺', 'inference', .4,
                          timestamp(relation['updated_at']), 'legacy', 0))
            conn.execute('INSERT INTO persona_migration_map VALUES(?,?,?)', (key, mid, now))
            counts['relationships'] += 1
        previous_calls = conn.execute("""SELECT * FROM (SELECT *,row_number() OVER
            (PARTITION BY user_id ORDER BY id DESC) AS position FROM tangtang_calls)
            WHERE position<=6 AND user_id>0""").fetchall()
        for call in previous_calls:
            counts['episodes'] += conn.execute('INSERT OR IGNORE INTO persona_episodes VALUES(?,?,?,?,?,?,?,?)',
                (f"legacy-call:{call['id']}", call['user_id'], call['group_id'], '', '',
                 '旧版记录中与此人有一次交流，具体对白仅限来源群上下文',
                 'legacy_history_not_reconfirmed', timestamp(call['created_at']))).rowcount
        conn.execute("INSERT OR IGNORE INTO person_memory_migrations VALUES('denia-v2-1')")
    if central:
        counts['growth'] = _migrate_growth(cognition, central, now)
    return counts


def _adopt(conn, user_id, kind, topic, content, status, created_at, scope_group=0):
    category = 'v2:' + kind + ':' + topic
    conn.execute("""INSERT OR IGNORE INTO person_semantic_memory
        (user_id,scope_group,category,content,normalized,tags,status,version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,1,?,?)""", (user_id, int(scope_group), category, content, normalize(content), json.dumps([topic]), status, created_at, created_at))
    mid = conn.execute('SELECT id FROM person_semantic_memory WHERE user_id=? AND scope_group=? AND category=? AND normalized=?',
                       (user_id, int(scope_group), category, normalize(content))).fetchone()[0]
    conn.execute('INSERT OR IGNORE INTO person_semantic_versions VALUES(?,?,?,?,?,?,?,?,?)',
                 (mid, 1, content, '', json.dumps([topic]), 0, '', 'legacy_adoption', created_at))
    return mid


def _legacy_source(conn, key, user_id, group_id, text, created_at):
    stamp = timestamp(created_at)
    conn.execute('INSERT OR IGNORE INTO persona_sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (key, user_id, group_id, key.split(':', 1)[-1], text, stamp, stamp, 'direct', 1, 'legacy'))


def _migrate_growth(cognition, central, now):
    with central.connect() as conn:
        rows = [dict(r) for r in conn.execute("""SELECT g.*,v.evidence_ids,v.created_at FROM growth g
            JOIN growth_versions v ON v.entry_id=g.id AND v.version=g.version
            WHERE g.persona='denia' AND g.group_id=0 AND g.enabled=1""")]
    adopted = 0
    for row in rows:
        key = 'growth:' + str(row['id'])
        ids = json.loads(row['evidence_ids'])
        evidence = central.cited_interactions('denia', 0, ids)
        if not evidence:
            continue
        with cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM persona_migration_map WHERE source=?', (key,)).fetchone():
                continue
            mid = _adopt(conn, 0, 'self_belief', row['topic'], row['content'], 'active', str(row['created_at']))
            conn.execute('INSERT OR IGNORE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,?)',
                         (mid, 'self_belief', row['topic'], '有证据的旧版观点', 'inference', .4, row['created_at'], 'legacy', 0))
            for ref in evidence:
                event_key = ref['request_id']
                _legacy_source(conn, event_key, ref['user_id'], ref['group_id'], ref['source'], ref['created_at'])
                conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)', (mid, 1, event_key, ref['source'], 'supports'))
            conn.execute('INSERT INTO persona_migration_map VALUES(?,?,?)', (key, mid, now))
            adopted += 1
    return adopted
