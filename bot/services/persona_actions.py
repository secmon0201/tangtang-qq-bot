"""Per-part action authority. Ambiguous external sends are never retried."""
from __future__ import annotations

import json
import time


class PersonaActions:
    def __init__(self, cognition):
        self.cognition = cognition

    def prepare(self, action_id, user_id, group_id, parts, expected, *, intent_id='', intent_version=None, now=None) -> bool:
        now = time.time() if now is None else now
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT 1 FROM persona_actions WHERE id=?', (action_id,)).fetchone():
                return False
            if not self.cognition.dependencies_current(conn, expected):
                return False
            if intent_id:
                row = conn.execute('SELECT * FROM persona_intents WHERE id=? AND user_id=?', (intent_id, user_id)).fetchone()
                if (not row or row['state'] not in {'open', 'eligible'} or row['due_at'] > now or
                        (row['expires_at'] and row['expires_at'] < now) or
                        (intent_version is not None and row['version'] != intent_version)):
                    return False
                conn.execute("""UPDATE persona_intents SET state='reserved',reserved_action=?,
                    version=version+1,updated_at=? WHERE id=?""", (action_id, now, intent_id))
                intent_version = row['version'] + 1
                self._intent_audit(conn, intent_id, action_id, now)
            conn.execute('INSERT INTO persona_actions VALUES(?,?,?,?,?,?,?,?,?)',
                         (action_id, user_id, group_id, 'prepared', json.dumps(expected), intent_id, now, now, intent_version or 0))
            conn.executemany('INSERT INTO persona_action_parts VALUES(?,?,?, ?,?,?,?)',
                             [(action_id, i, text, 'prepared', '', '', now) for i, text in enumerate(parts)])
        return True

    def start(self, action_id, part, *, now=None) -> bool:
        now = time.time() if now is None else now
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            action = conn.execute('SELECT * FROM persona_actions WHERE id=?', (action_id,)).fetchone()
            if not action or action['state'] not in {'prepared', 'partial'}:
                return False
            valid = self.cognition.dependencies_current(conn, json.loads(action['expected']))
            if action['intent_id']:
                intent = conn.execute('SELECT state,reserved_action,version FROM persona_intents WHERE id=?', (action['intent_id'],)).fetchone()
                valid = valid and bool(intent and intent['state'] == 'reserved' and intent['reserved_action'] == action_id
                                       and intent['version'] == action['intent_version'])
            if not valid:
                conn.execute("UPDATE persona_actions SET state='superseded',updated_at=? WHERE id=?", (now, action_id))
                self._release(conn, action, now)
                return False
            changed = conn.execute("""UPDATE persona_action_parts SET state='sending',updated_at=?
                WHERE action_id=? AND part=? AND state='prepared'""", (now, action_id, part)).rowcount
            if changed:
                conn.execute("UPDATE persona_actions SET state='sending',updated_at=? WHERE id=?", (now, action_id))
            return bool(changed)

    def result(self, action_id, part, *, message_id='', error='', definite_failure=False, now=None):
        now = time.time() if now is None else now
        status = 'confirmed' if message_id else 'failed' if definite_failure else 'unknown'
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            action = conn.execute('SELECT * FROM persona_actions WHERE id=?', (action_id,)).fetchone()
            row = conn.execute('SELECT * FROM persona_action_parts WHERE action_id=? AND part=?', (action_id, part)).fetchone()
            if not action or not row or row['state'] != 'sending':
                return
            conn.execute('UPDATE persona_action_parts SET state=?,message_id=?,error=?,updated_at=? WHERE action_id=? AND part=?',
                         (status, str(message_id), error[:80], now, action_id, part))
            if message_id:
                conn.execute('INSERT OR IGNORE INTO persona_exposures VALUES(?,?,?,?,?,?)',
                             (action_id, part, action['group_id'], action['user_id'], str(message_id), now))
                # Store the actual sent part, not the generated full draft.
                conn.execute('INSERT OR IGNORE INTO persona_episodes VALUES(?,?,?,?,?,?,?,?)',
                             (f'action:{action_id}:{part}', action['user_id'], action['group_id'], '', action_id,
                              row['text'], 'delivered_not_read', now))
            states = [r[0] for r in conn.execute('SELECT state FROM persona_action_parts WHERE action_id=?', (action_id,))]
            aggregate = ('confirmed' if all(s == 'confirmed' for s in states) else 'unknown' if 'unknown' in states
                         else 'partial' if 'confirmed' in states else 'failed' if 'failed' in states else 'prepared')
            conn.execute('UPDATE persona_actions SET state=?,updated_at=? WHERE id=?', (aggregate, now, action_id))
            if aggregate == 'confirmed' and action['intent_id']:
                conn.execute("""UPDATE persona_intents SET state='awaiting_result',version=version+1,
                    updated_at=? WHERE id=? AND reserved_action=? AND state='reserved'""", (now, action['intent_id'], action_id))
                self._intent_audit(conn, action['intent_id'], action_id, now)
            elif status == 'failed':
                self._release(conn, action, now)

    @staticmethod
    def _intent_audit(conn, intent_id, source, now):
        row = conn.execute('SELECT version,state FROM persona_intents WHERE id=?', (intent_id,)).fetchone()
        conn.execute('INSERT OR IGNORE INTO persona_intent_versions VALUES(?,?,?,?,?)',
                     (intent_id, row['version'], row['state'], source, now))

    @staticmethod
    def _release(conn, action, now):
        # Any confirmed/unknown part may already contain a follow-up. Keep the
        # reservation unless no external effect could have happened.
        ambiguous = conn.execute("""SELECT 1 FROM persona_action_parts WHERE action_id=?
            AND state IN ('confirmed','unknown','sending') LIMIT 1""", (action['id'],)).fetchone()
        if not ambiguous and action['intent_id']:
            conn.execute("""UPDATE persona_intents SET state='open',reserved_action='',version=version+1,
                updated_at=? WHERE id=? AND reserved_action=? AND state='reserved'""", (now, action['intent_id'], action['id']))
            PersonaActions._intent_audit(conn, action['intent_id'], action['id'], now)

    def recover(self, now=None):
        """Startup only, before any sender exists; never poll live sends."""
        now = time.time() if now is None else now
        with self.cognition.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE persona_action_parts SET state='unknown',error='restart_during_send',updated_at=? WHERE state='sending'", (now,))
            conn.execute("""UPDATE persona_actions SET state='unknown',updated_at=? WHERE id IN
                (SELECT action_id FROM persona_action_parts WHERE state='unknown')""", (now,))
            rows = conn.execute("SELECT * FROM persona_actions WHERE state='prepared'").fetchall()
            for row in rows:
                conn.execute("UPDATE persona_actions SET state='superseded',updated_at=? WHERE id=?", (now, row['id']))
                self._release(conn, row, now)
            conn.execute("""UPDATE persona_intents SET state='expired_unknown',version=version+1,updated_at=?
                WHERE expires_at>0 AND expires_at<? AND state IN ('open','eligible','deferred','awaiting_result')""", (now, now))

    def diagnostics(self) -> dict:
        with self.cognition.connect() as conn:
            return {'actions': dict(conn.execute('SELECT state,count(*) FROM persona_actions GROUP BY state')),
                    'intents': dict(conn.execute('SELECT state,count(*) FROM persona_intents GROUP BY state')),
                    'claims': dict(conn.execute('SELECT kind,count(*) FROM persona_claim_metadata GROUP BY kind'))}
