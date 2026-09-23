"""Persistent hourly admission and accounting for memory, profiles and summaries."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from bot.services.persona_capacity import background_request, CapacityUnavailable


DEFAULT_POLICY = {
    'summary_requests': 120, 'summary_tokens': 600000,
    'memory_requests': 60, 'memory_tokens': 300000,
    'profile_requests': 80, 'profile_tokens': 800000,
    'history_requests': 12, 'history_tokens': 150000,
    'summary_wait_seconds': 240, 'summary_min_messages': 40,
    'summary_batch_messages': 60,
    'summary_input_chars': 18000, 'memory_wait_seconds': 180,
    'memory_batch_messages': 48, 'profile_cooldown_seconds': 900,
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS background_work_calls(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, batch TEXT NOT NULL,
 historical INTEGER NOT NULL, sources INTEGER NOT NULL,
 started_at REAL NOT NULL, completed_at REAL NOT NULL DEFAULT 0,
 status TEXT NOT NULL, charged_tokens INTEGER NOT NULL, usage TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_background_work_window ON background_work_calls(started_at,kind);
'''


class WorkDeferred(Exception):
    """Admission was postponed without calling a model or rejecting evidence."""


def batch_key(sources):
    parts = [(s.get('event_key', s.get('id')), s.get('revision', 1),
              s.get('reviewed_chars', len(s.get('text', '')))) for s in sources]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


class BackgroundWork:
    def __init__(self, store):
        self.store = store
        with store.connect() as conn:
            conn.executescript(SCHEMA)

    def policy(self):
        saved = self.store.option('background_work_policy', {})
        return {k: max(0, int(saved.get(k, v))) for k, v in DEFAULT_POLICY.items()}

    def reserve(self, kind, batch, sources, historical, tokens, now=None):
        now = time.time() if now is None else now
        policy = self.policy()
        with self.store.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for scope, predicate, args in ((kind, 'kind=?', (kind,)),
                                           ('history', 'historical=1', ())):
                if scope == 'history' and not historical:
                    continue
                count, used = conn.execute(
                    'SELECT count(*),COALESCE(sum(charged_tokens),0) FROM background_work_calls '
                    "WHERE started_at>? AND status<>'deferred_queue' AND " + predicate, (now - 3600, *args)).fetchone()
                if count >= policy[scope + '_requests'] or used + tokens > policy[scope + '_tokens']:
                    raise WorkDeferred('hourly_' + scope + '_budget')
            call_id = uuid.uuid4().hex
            conn.execute('INSERT INTO background_work_calls '
                         '(id,kind,batch,historical,sources,started_at,status,charged_tokens) VALUES(?,?,?,?,?,?,?,?)',
                         (call_id, kind, batch, int(historical), sources, now, 'reserved', tokens))
        return call_id

    def finish(self, call_id, status, usage=None):
        usage = usage or {}
        safe = {k: v for k, v in usage.items() if k in {
            'prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens',
            'cache_read_tokens', 'cache_miss_tokens', 'latency_ms'} and isinstance(v, (int, float))}
        total = safe.get('total_tokens')
        if total is None and 'prompt_tokens' in safe and 'completion_tokens' in safe:
            total = safe['prompt_tokens'] + safe['completion_tokens']
        with self.store.connect() as conn:
            conn.execute('UPDATE background_work_calls SET status=?,completed_at=?,usage=?, '
                         'charged_tokens=COALESCE(?,charged_tokens) WHERE id=?',
                         (status, time.time(), json.dumps(safe), max(0, int(total)) if total is not None else None, call_id))

    async def generate(self, provider, config, instruction, prompt, *, kind, sources, historical=False):
        # UTF-8 bytes are a conservative input reservation; unknown/failed usage
        # keeps the reservation so retries and interrupted calls cannot evade caps.
        reserve = len((instruction + prompt).encode('utf-8')) + config.max_output_tokens + 256
        call_id = self.reserve(kind, batch_key(sources), len(sources), historical, reserve)
        token = background_request.set(True)
        try:
            output, usage = await provider.generate(config, instruction, prompt)
            self.finish(call_id, 'returned', usage)
            return output, {**usage, 'background_request_id': call_id}
        except CapacityUnavailable as exc:
            self.finish(call_id, 'deferred_queue', {'total_tokens': 0})
            raise WorkDeferred('local_capacity_busy') from exc
        except BaseException as exc:
            self.finish(call_id, type(exc).__name__)
            raise
        finally:
            background_request.reset(token)
