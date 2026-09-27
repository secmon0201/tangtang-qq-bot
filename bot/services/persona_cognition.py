"""One versioned memory authority over the existing semantic memory tables."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from bot.services.persona_cognition_schema import SCHEMA
from bot.services.persona_contracts import (
    EXTRACTOR, KINDS, MergeResult, TurnSnapshot, bounded_text, evidence_for,
)
from bot.services.persona_memory_store import PersonMemoryStore, normalize
from bot.services.persona_memory_contract import semantic_topics
from bot.services.persona_profile_store import ProfileStore, enqueue, ensure_profile_schema
from bot.services.persona_impressions import impression_requested


class CognitionStore:
    def __init__(self, db):
        # New Denia memory is group-local. Existing global rows remain for
        # migration/audit but are not recalled for a real group.
        self.people = PersonMemoryStore(db, global_personal=False)
        self.blocked_users = lambda group_id: db.blocked_users(group_id)

    @contextmanager
    def connect(self):
        with self.people.connect() as conn:
            conn.executescript(SCHEMA)
            state_columns = {row[1] for row in conn.execute('PRAGMA table_info(persona_state_factors)')}
            if 'scope_group' not in state_columns:
                conn.execute(
                    'ALTER TABLE persona_state_factors ADD COLUMN scope_group INTEGER NOT NULL DEFAULT 0'
                )
            ensure_profile_schema(conn)
            yield conn

    def import_sources(self, sources) -> None:
        with self.connect() as conn:
            for row in sources:
                if int(row['user_id']) in self.blocked_users(int(row['group_id'])):
                    continue
                if impression_requested(row['text']):
                    continue
                changed = conn.execute("""INSERT INTO persona_sources VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_key) DO UPDATE SET attribution=excluded.attribution,
                    revision=excluded.revision WHERE excluded.revision>persona_sources.revision""",
                    tuple(row[k] for k in ('event_key', 'user_id', 'group_id', 'message_id', 'text',
                                          'occurred_at', 'received_at', 'attribution', 'revision', 'route_version'))).rowcount
                if changed:
                    now = time.time()
                    priority = 1
                    if row['received_at'] >= now - 86400:
                        priority = 3 if row['attribution'] == 'direct' else 2
                    enqueue(conn, row['user_id'], now, priority=priority)
                if row.get('reply_to'):
                    part = conn.execute("""SELECT p.action_id,p.part FROM persona_action_parts p
                        JOIN persona_actions a ON a.id=p.action_id
                        WHERE a.group_id=? AND p.message_id=? AND p.state='confirmed'""",
                        (row['group_id'], str(row['reply_to']))).fetchone()
                    if part:
                        conn.execute('INSERT OR IGNORE INTO persona_feedback VALUES(?,?,?,?,?)',
                                     (row['event_key'], row['user_id'], part['action_id'], part['part'], row['occurred_at']))

    def processed(self, sources) -> bool:
        with self.connect() as conn:
            return bool(sources) and all(conn.execute("""SELECT 1 FROM persona_processing_receipts
                WHERE event_key=? AND revision=? AND extractor=?""",
                (s['event_key'], s['revision'], EXTRACTOR)).fetchone() for s in sources)

    def snapshot(self, turn_id, user_id, group_id, sources, query='', now=None) -> TurnSnapshot:
        now = time.time() if now is None else now
        self.import_sources(sources)
        with self.connect() as conn:
            conn.execute('BEGIN')
            base = """SELECT m.id,m.user_id,m.content,m.version,
                m.updated_at,c.kind,c.topic,c.applicability,c.assertion_type,c.confidence,c.occurred_at
                FROM person_semantic_memory m JOIN persona_claim_metadata c ON c.memory_id=m.id
                WHERE m.user_id IN (?,0) AND m.status IN ('active','candidate')
                AND ((m.user_id=0 AND m.scope_group=0) OR (m.user_id=? AND m.scope_group=?))
                AND (c.kind<>'impression' OR EXISTS (SELECT 1 FROM persona_profile_approved p
                    WHERE p.memory_id=m.id AND p.version=m.version))
                AND (c.valid_until=0 OR c.valid_until>?)"""
            # Search all this person's records, not just a recent candidate
            # window. Exact Chinese bigrams and topic synonyms need no service.
            terms = sorted(semantic_topics(query)) + list(dict.fromkeys(
                query[i:i + 2] for i in range(max(0, len(query) - 1)) if query[i:i + 2].strip()))[:16]
            rows = []
            if terms:
                score = '+'.join('(instr(m.content,?)>0 OR instr(c.topic,?)>0)' for _ in terms)
                args = tuple(v for term in terms for v in (term, term))
                rows.extend(dict(r) for r in conn.execute(base + f' ORDER BY ({score}) DESC,m.id DESC LIMIT 12', (user_id, user_id, group_id, now, *args)))
            for kinds in (('fact',), ('impression', 'relationship'), ('self_belief',)):
                rows.extend(dict(r) for r in conn.execute(base + f" AND c.kind IN ({','.join('?' for _ in kinds)}) ORDER BY m.id DESC LIMIT 4",
                                                         (user_id, user_id, group_id, now, *kinds)))
            claims = list({r['id']: r for r in rows}.values())[:24]
            restrictions = [r[0] for r in conn.execute(
                'SELECT needle FROM person_restrictions WHERE user_id=? AND scope_group IN (0,?) AND active=1',
                (user_id, group_id))]
            claims = [r for r in claims if not any(n in normalize(r['content']) for n in restrictions)]
            for r in claims:
                r['basis'] = [dict(e) for e in conn.execute("""SELECT quote,stance FROM persona_claim_support
                    WHERE memory_id=? AND version=? LIMIT 3""", (r['id'], r['version']))]
            states = []
            for row in conn.execute(
                "SELECT * FROM persona_state_factors WHERE "
                "(user_id=0 AND scope_group=0) OR (user_id=? AND scope_group=?) "
                "ORDER BY created_at DESC LIMIT 24",
                (user_id, group_id),
            ):
                strength = row['strength'] * 2 ** (-max(0, now - row['created_at']) / row['half_life'])
                if abs(strength) >= .01:
                    states.append({'topic': row['topic'], 'cause': row['cause'], 'label': row['label'],
                                   'target': 'self' if row['user_id'] == 0 else 'person', 'strength': round(strength, 3)})
            episodes = [dict(r) for r in conn.execute("""SELECT summary,outcome,created_at,group_id,action_id FROM persona_episodes
                WHERE user_id=? AND group_id=? ORDER BY created_at DESC LIMIT 6""", (user_id, group_id))]
            episodes = [r for r in episodes if not any(n in normalize(r['summary']) for n in restrictions)]
            # Legacy intents have no group scope, so they cannot enter a
            # group-local prompt until their table is migrated.
            intents = []
            total_strength = sum(abs(s['strength']) for s in states)
            if total_strength > .6:
                for state in states:
                    state['strength'] = round(state['strength'] * .6 / total_strength, 3)
        return TurnSnapshot(turn_id, user_id, group_id, list(sources), claims, states, episodes, intents,
                            {str(r['id']): r['version'] for r in claims})

    @staticmethod
    def dependencies_current(conn, expected) -> bool:
        for key, version in expected.items():
            row = conn.execute('SELECT version,status FROM person_semantic_memory WHERE id=?', (int(key),)).fetchone()
            if not row or row['version'] != version or row['status'] not in {'active', 'candidate'}:
                return False
        return True

    def current(self, expected) -> bool:
        with self.connect() as conn:
            return self.dependencies_current(conn, expected)

    def group_impression(self, user_id: int, group_id: int) -> str:
        """Return only confirmed facts written from this group."""

        with self.connect() as conn:
            rows = conn.execute(
                "SELECT content FROM person_semantic_memory "
                "WHERE user_id=? AND scope_group=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 8",
                (int(user_id), int(group_id)),
            ).fetchall()
        if not rows:
            return "这个群里还没有可确认的个人资料。"
        return "这个群里我记得的你：\n" + "\n".join(f"· {row[0]}" for row in rows)

    def merge(self, proposal: dict, snapshot: TurnSnapshot, now=None) -> MergeResult:
        now = time.time() if now is None else now
        result = MergeResult(expected=dict(snapshot.expected))
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for key, reducer in (('claims', self._claim), ('states', self._state), ('intents', self._intent)):
                for raw in proposal.get(key, [])[:8]:
                    conn.execute('SAVEPOINT patch')
                    try:
                        accepted = reducer(conn, raw, snapshot, now)
                        if accepted:
                            result.accepted.append(accepted)
                            if accepted.startswith('s:'):
                                mid = accepted[2:]
                                result.expected[mid] = conn.execute('SELECT version FROM person_semantic_memory WHERE id=?', (mid,)).fetchone()[0]
                            elif accepted.startswith('intent:'):
                                iid = accepted[7:]
                                result.intent_versions[iid] = conn.execute('SELECT version FROM persona_intents WHERE id=?', (iid,)).fetchone()[0]
                        conn.execute('RELEASE patch')
                    except (ValueError, TypeError, KeyError, OverflowError, sqlite3.IntegrityError) as exc:
                        conn.execute('ROLLBACK TO patch')
                        conn.execute('RELEASE patch')
                        result.rejected.append('memory_constraint_conflict' if isinstance(exc, sqlite3.IntegrityError)
                                               else str(exc)[:100])
            for s in snapshot.sources:
                if not result.rejected:
                    conn.execute('INSERT OR IGNORE INTO persona_processing_receipts VALUES(?,?,?,?)',
                                 (s['event_key'], s['revision'], EXTRACTOR, now))
                conn.execute('INSERT OR IGNORE INTO persona_episodes VALUES(?,?,?,?,?,?,?,?)',
                             ('observation:' + s['event_key'], s['user_id'], s['group_id'], s['event_key'], '',
                              '收到一次直接交流' if s['attribution'] == 'direct' else '观察到一次本人发言',
                              'observed', s['occurred_at']))
            conn.execute('INSERT INTO persona_cognition_reviews(turn_id,status,detail,created_at) VALUES(?,?,?,?)',
                         (snapshot.turn_id, 'merged', json.dumps({'accepted': result.accepted, 'rejected': result.rejected}), now))
            self._portrait(conn, snapshot.user_id, now)
            self._portrait(conn, 0, now)
        return result

    def _claim(self, conn, raw, snapshot, now):
        kind = raw.get('kind')
        if kind not in KINDS or raw.get('operation', 'observe') not in {'observe', 'revise'}:
            raise ValueError('invalid_kind')
        refs = evidence_for(raw, snapshot, direct_only=kind in {'relationship', 'self_belief'})
        if kind == 'impression':
            # Chat-generated interpretations are never authoritative. The raw
            # sources are already queued for independent generation and review.
            enqueue(conn, snapshot.user_id, now)
            return 'queued:' + str(snapshot.user_id)
        statement = bounded_text(raw, 'statement', 240)
        topic = bounded_text(raw, 'topic', 80)
        applicability = bounded_text(raw, 'applicability', 180, optional=True)
        if kind == 'self_belief' and (re.search(r'我是|我叫|核心|身份|权限|管理员|女友|男友|永远', statement)
                or any(re.search(r'你是|你必须|变成|从现在起|记住你', s['text']) for s, _, _ in refs)):
            raise ValueError('immutable_core_or_imposed_identity')
        assertion = raw.get('assertion_type', 'inference')
        if assertion not in {'self_report', 'request', 'inference'} or (kind != 'fact' and assertion != 'inference'):
            raise ValueError('invalid_assertion_type')
        if kind == 'fact':
            # Literal first-person assertions, including negation. Inference is
            # stored as inference, never promoted by substring validation alone.
            if assertion not in {'self_report', 'request'} or not any(statement == quote for _, quote, _ in refs):
                raise ValueError('facts_require_literal_self_report')
            personal = bool(re.match(r'^(?:(?:今天|昨天|上周|上次|刚才|之前|最近|以后|等|这次)[^。]{0,14})?'
                                     r'我(?!们|(?:的)?(?:朋友|同学|同事|爸爸|妈妈|家人))|^(?:叫我|称呼我)|^[^。]{0,16}(?:让我|令我)', statement))
            directive = bool(re.match(r'^(?:请|先|以后|这次|暂时)?(?:不要|别|不再|等我|叫我|称呼我)', statement))
            if directive:
                assertion = 'request'
            if ((not personal and not directive) or
                    re.search(r'他说|她说|转发|引用|假如|如果|开玩笑|[“”"？?]', refs[0][0]['text'])):
                raise ValueError('not_asserted_by_subject')
            for source, quote, _ in refs:
                before, after = source['text'].split(quote, 1)
                if re.search(r'不是|并非|没说|听说|假装|举例', before[-16:]) or re.match(r'[，, ]*(?:是假的|才怪|是玩笑)', after):
                    raise ValueError('not_asserted_by_subject')
        subject = 0 if kind == 'self_belief' else snapshot.user_id
        occurred = max(s['occurred_at'] for s, _, _ in refs)
        weight = .6 if any(s['attribution'] == 'direct' for s, _, _ in refs) else .3
        memory_id = raw.get('id')
        old = None
        if memory_id is not None:
            old = conn.execute("""SELECT m.*,c.occurred_at,c.kind,c.topic FROM person_semantic_memory m
                JOIN persona_claim_metadata c ON c.memory_id=m.id WHERE m.id=?""", (int(memory_id),)).fetchone()
            if (not old or old['user_id'] != subject or old['kind'] != kind or old['topic'] != topic or
                    old['version'] != raw.get('expected_version') or raw.get('operation') != 'revise'):
                raise ValueError('claim_version_or_owner_conflict')
            if occurred < old['occurred_at']:
                raise ValueError('out_of_order_correction')
        else:
            old = conn.execute("""SELECT m.*,c.occurred_at,c.kind,c.topic FROM person_semantic_memory m
                JOIN persona_claim_metadata c ON c.memory_id=m.id
                WHERE m.user_id=? AND c.kind=? AND c.topic=? AND c.applicability=?
                AND m.scope_group=?
                AND m.status IN ('active','candidate') LIMIT 1""", (subject, kind, topic, applicability,
                                                                        snapshot.group_id if subject else 0)).fetchone()
            if old and old['content'] != statement:
                raise ValueError('existing_topic_requires_revision')
        if old and old['content'] == statement:
            memory_id, version = old['id'], old['version']
        else:
            version = old['version'] + 1 if old else 1
            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
            if old:
                memory_id = old['id']
                conn.execute("""UPDATE person_semantic_memory SET content=?,normalized=?,version=?,updated_at=? WHERE id=?""",
                             (statement, normalize(statement), version, stamp, memory_id))
            else:
                memory_id = conn.execute("""INSERT INTO person_semantic_memory
                    (user_id,scope_group,category,content,normalized,tags,status,version,created_at,updated_at)
                    VALUES(?,?,?,?,?,?, 'active',1,?,?)""",
                    (subject, snapshot.group_id if subject else 0, 'v2:' + kind + ':' + topic,
                     statement, normalize(statement), json.dumps([topic], ensure_ascii=False), stamp, stamp)).lastrowid
            source, quote, _ = refs[0]
            conn.execute('INSERT INTO person_semantic_versions VALUES(?,?,?,?,?,?,?,?,?)',
                         (memory_id, version, statement, quote, json.dumps([topic]), source['group_id'],
                          source['message_id'], 'revise' if old else 'observe', stamp))
        conn.execute('INSERT OR REPLACE INTO persona_claim_metadata VALUES(?,?,?,?,?,?,?,?,0)',
                     (memory_id, kind, topic, applicability, assertion, weight, occurred,
                      'direct' if weight == .6 else 'ambient'))
        for source, quote, stance in refs:
            conn.execute('INSERT OR IGNORE INTO persona_claim_support VALUES(?,?,?,?,?)',
                         (memory_id, version, source['event_key'], quote, stance))
            conn.execute('INSERT OR IGNORE INTO person_semantic_evidence VALUES(?,?,?,?,?,?,?)',
                         (memory_id, source['event_key'], source['group_id'], source['message_id'], quote, 0, str(now)))
        # Replays, duplicate snippets and direct-attribution upgrades never
        # become independent supporting events. Revisions start their own basis.
        observed = conn.execute("""SELECT count(DISTINCT e.event_key),max(s.attribution='direct')
            FROM persona_claim_support e JOIN persona_sources s ON s.event_key=e.event_key
            WHERE e.memory_id=? AND e.version=?""", (memory_id, version)).fetchone()
        base = .6 if observed[1] else .3
        confidence = min(.9 if observed[1] else .55, base + .08 * math.log2(max(1, observed[0])))
        conn.execute('UPDATE persona_claim_metadata SET confidence=?,provenance=? WHERE memory_id=?',
                     (confidence, 'direct' if observed[1] else 'ambient', memory_id))
        return f's:{memory_id}'

    @staticmethod
    def _state(conn, raw, snapshot, now):
        refs = evidence_for(raw, snapshot, direct_only=True)
        topic, label = bounded_text(raw, 'topic', 80), bounded_text(raw, 'label', 120)
        strength, half_life = float(raw.get('strength', 0)), float(raw.get('half_life', 1800))
        if not math.isfinite(strength) or not math.isfinite(half_life):
            raise ValueError('nonfinite_state')
        source = refs[0][0]
        target = raw.get('target', 'person')
        if target not in {'person', 'self'}:
            raise ValueError('invalid_state_target')
        subject, bound = snapshot.user_id, .4
        if target == 'self':
            dimension = raw.get('dimension')
            if dimension not in {'energy', 'attention'}:
                raise ValueError('global_state_must_not_spread_personal_emotion')
            subject, bound = 0, .15
            topic = 'self:' + dimension + ':' + topic
        key = f"{source['event_key']}:{topic}"
        conn.execute('INSERT OR IGNORE INTO persona_state_factors '
                     '(id,user_id,scope_group,topic,cause,label,strength,half_life,created_at) '
                     'VALUES(?,?,?,?,?,?,?,?,?)',
                     (key, subject, snapshot.group_id if subject else 0, topic, source['event_key'], label,
                      max(-bound, min(bound, strength)), max(60, min(21600, half_life)), source['occurred_at']))
        return 'state:' + key

    @staticmethod
    def _intent(conn, raw, snapshot, now):
        refs = evidence_for(raw, snapshot, direct_only=True)
        topic = bounded_text(raw, 'topic', 80)
        description = bounded_text(raw, 'description', 240)
        state = raw.get('state', 'open')
        if state not in {'open', 'deferred', 'withdrawn', 'fulfilled'}:
            raise ValueError('invalid_intent_transition')
        old = conn.execute('SELECT * FROM persona_intents WHERE user_id=? AND topic=?', (snapshot.user_id, topic)).fetchone()
        if old:
            if old['source_key'] == refs[0][0]['event_key'] and old['description'] == description and old['state'] == state:
                return 'intent:' + old['id']
            if old['id'] != raw.get('id') or old['version'] != raw.get('expected_version'):
                raise ValueError('intent_version_conflict')
            if old['state'] in {'fulfilled', 'withdrawn'} and state == 'open':
                raise ValueError('closed_intent_requires_new_topic')
        elif state not in {'open', 'deferred'}:
            raise ValueError('missing_intent')
        source = refs[0][0]
        if state == 'fulfilled' and not re.search(r'完成|做好|改好|结束|解决|已经|弄好', source['text']):
            raise ValueError('missing_result_evidence')
        due, expires = float(raw.get('due_at', 0)), float(raw.get('expires_at', 0))
        if not all(math.isfinite(v) and v >= 0 for v in (due, expires)):
            raise ValueError('invalid_due_time')
        iid = old['id'] if old else f'{snapshot.user_id}:{topic}'
        version = old['version'] + 1 if old else 1
        conn.execute('INSERT OR REPLACE INTO persona_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (iid, snapshot.user_id, topic, description, source['event_key'], state, due, expires, version, '', now))
        conn.execute('INSERT INTO persona_intent_versions VALUES(?,?,?,?,?)', (iid, version, state, source['event_key'], now))
        return 'intent:' + iid

    @staticmethod
    def _portrait(conn, user_id, now):
        if user_id:
            return  # Personal portraits are published only by ProfileStore.
        rows = conn.execute("""SELECT m.id,m.content,m.version FROM person_semantic_memory m
            JOIN persona_claim_metadata c ON c.memory_id=m.id WHERE m.user_id=?
            AND c.kind IN ('impression','relationship','self_belief') AND m.status='active' ORDER BY m.id DESC LIMIT 8""", (user_id,)).fetchall()
        conn.execute('INSERT OR REPLACE INTO persona_portraits VALUES(?,?,?,?)',
                     (user_id, '\n'.join(r['content'] for r in rows), json.dumps({str(r['id']): r['version'] for r in rows}), now))

    def own_impression(self, user_id: int) -> str:
        return ProfileStore(self).display(user_id)
