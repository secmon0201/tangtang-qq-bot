"""Incremental group-topic summaries, independent from personal memory."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
import re
import time
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from nonebot import logger

from bot.services.tangtang_db import TangtangDb
from bot.services.background_work import BackgroundWork, WorkDeferred, DEFAULT_POLICY
from bot.services.persona_background_retry import BackgroundRetry
from bot.services.group_summary_batch import INSTRUCTION, parse_batch


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_\-]{2,}")


def _terms(text: str) -> tuple[str, ...]:
    """Short local term set used for routing, never for personal assertions."""

    normalized = str(text or "")
    tokens = set(_TOKEN.findall(normalized))
    chinese = "".join(_TOKEN.findall(normalized))
    if len(chinese) >= 2:
        tokens.update(chinese[i:i + 2] for i in range(len(chinese) - 1))
    return tuple(sorted(token for token in tokens if len(token) >= 2))[:64]


def _lines(value: str, *, limit: int, maximum: int = 120) -> tuple[str, ...]:
    result: list[str] = []
    for raw in str(value or "").split("\n"):
        item = " ".join(raw.split()).strip()[:maximum]
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return tuple(result)


def _one_line(value: str, *, maximum: int = 4000) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


@dataclass(frozen=True, slots=True)
class SummaryTopic:
    topic_id: int | None
    title: str
    summary: str
    keywords: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    state: str = "active"


def topic_from_row(row: Mapping[str, Any]) -> SummaryTopic:
    return SummaryTopic(
        topic_id=int(row["topic_id"]),
        title=str(row["title"]),
        summary=str(row["summary"]),
        keywords=_lines(str(row.get("keywords") or ""), limit=8),
        participants=_lines(str(row.get("participants") or ""), limit=12),
        unresolved=_lines(str(row.get("unresolved") or ""), limit=8),
        state=str(row.get("state") or "active"),
    )


def parse_summary_json(text: str) -> SummaryTopic:
    """Parse the strict merge contract; structural errors are explicit."""

    clean = _JSON_FENCE.sub("", str(text or "").strip()).strip()
    payload = json.loads(clean)
    if not isinstance(payload, dict):
        raise ValueError("summary_not_object")
    title = _one_line(payload.get("title", ""), maximum=120)
    summary = _one_line(payload.get("summary", ""), maximum=4000)
    if not title or not summary:
        raise ValueError("summary_missing_text")
    state = str(payload.get("state", "active")).strip().lower()
    if state not in {"active", "cooling"}:
        state = "active"
    keywords = tuple(str(item).strip()[:40] for item in payload.get("keywords", [])
                     if str(item).strip())[:6]
    participants = tuple(str(item).strip()[:80] for item in payload.get("participants", [])
                         if str(item).strip())[:10]
    unresolved = tuple(str(item).strip()[:120] for item in payload.get("unresolved", [])
                       if str(item).strip())[:6]
    return SummaryTopic(None, title, summary, keywords, participants, unresolved, state)


class GroupSummaryService:
    """Durable topic state plus one bounded model call per group batch."""

    def __init__(
        self,
        db: TangtangDb,
        provider: Any,
        loader: Any,
        *,
        chat_id: Callable[[], str],
        central=None,
        enabled=lambda group_id: True,
    ) -> None:
        self.db = db
        self.provider = provider
        self.loader = loader
        self._chat_id = chat_id
        self.enabled = enabled
        self.work = BackgroundWork(central) if central is not None else None
        self.retry = BackgroundRetry(central, key='summary_provider_retry', base_delay=60) if central is not None else None

    async def apply_batch(self, group_id, rows):
        config = replace(self.loader.load(), timeout_seconds=120, max_output_tokens=6000,
                         max_response_chars=24000, reasoning_effort='low')
        if not config.enabled or not config.group_summary_enabled or not self.enabled(group_id):
            raise WorkDeferred('summary_disabled')
        if self.retry and self.retry.blocked_status(config, time.time()):
            raise WorkDeferred('summary_provider_backoff')
        blocked = self.db.blocked_users(group_id)
        evidence = [r for r in rows if int(r['user_id']) not in blocked and str(r['text']).strip()]
        if not evidence:
            self.db.group_summary_commit_batch(group_id, [], [r['id'] for r in rows], now=self._chat_id())
            return
        # Favor topics matching this entire batch, then recently active topics.
        terms = set(t for r in evidence for t in _terms(str(r['text'])))
        topics = self.db.group_summary_sources(group_id)
        topics.sort(key=lambda t: sum(term in (t['title'] + t['summary'] + t['keywords']) for term in terms), reverse=True)
        topics = topics[:12]
        prompt = json.dumps({'topics': [{k: t[k] for k in ('topic_id', 'title', 'summary', 'unresolved')}
                                        for t in topics],
                             'messages': [{k: r[k] for k in ('id', 'nickname', 'created_at', 'text')} for r in evidence]},
                            ensure_ascii=False)
        try:
            if self.work:
                output, usage = await self.work.generate(self.provider, config, INSTRUCTION, prompt,
                                                         kind='summary', sources=evidence)
            else:
                output, usage = await self.provider.generate(config, INSTRUCTION, prompt)
            payload = parse_batch(output, [r['id'] for r in evidence], {t['topic_id'] for t in topics})
            updates = []
            for raw in payload['updates']:
                topic = parse_summary_json(json.dumps(raw, ensure_ascii=False))
                updates.append(dict(topic_id=raw['topic_id'] or None, title=topic.title, summary=topic.summary,
                                    keywords=topic.keywords, participants=topic.participants,
                                    unresolved=topic.unresolved, state=topic.state,
                                    message_ids=tuple(raw['message_ids'])))
            current = self.loader.load()
            if not current.enabled or not current.group_summary_enabled or not self.enabled(group_id):
                raise WorkDeferred('summary_disabled')
            if self.db.blocked_users(group_id) != blocked:
                raise ValueError('summary_blacklist_changed')
            self.db.group_summary_commit_batch(group_id, updates, [r['id'] for r in rows], now=self._chat_id())
            self.db.group_summary_archive_excess(group_id, limit=config.group_summary_topic_limit)
            if self.work:
                self.work.store.record_job('group_summary', group_id, time.time(), usage, 'completed')
                self.retry.succeeded()
        except WorkDeferred:
            raise
        except Exception as exc:
            if self.retry:
                # Local validation/storage failure belongs to this batch only.
                import httpx
                if isinstance(exc, (httpx.HTTPError, TimeoutError)):
                    self.retry.failed(config, exc, time.time())
            raise

    def pending(self, group_id: int, limit: int = 400) -> list[dict[str, Any]]:
        return self.db.group_summary_pending(group_id, limit=limit)



class GroupSummaryWorker:
    """One atomic multi-topic request per ready group, with persistent backoff."""

    def __init__(self, service: GroupSummaryService, *, batch_messages: int = 200) -> None:
        self.service = service
        self.batch_messages = max(10, int(batch_messages))
        self.lock = asyncio.Lock()
        with service.db._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS group_summary_schedule "
                         "(group_id INTEGER PRIMARY KEY, next_attempt REAL NOT NULL, failures INTEGER NOT NULL)")

    async def tick(self, active_groups: Iterable[int]) -> int:
        if self.lock.locked():
            return 0
        policy = self.service.work.policy() if self.service.work else DEFAULT_POLICY
        applied = 0
        async with self.lock:
            for group_id in active_groups:
                now = datetime.fromisoformat(self.service._chat_id()).timestamp()
                with self.service.db._connect() as conn:
                    state = conn.execute('SELECT * FROM group_summary_schedule WHERE group_id=?', (group_id,)).fetchone()
                if state and state['next_attempt'] > now:
                    continue
                # Repeatedly invalid/oversized output should get a smaller batch,
                # not the same increasingly large evidence payload forever.
                maximum = min(self.batch_messages, max(1, policy['summary_batch_messages']))
                read_limit = max(1, maximum // (2 ** min(state['failures'] if state else 0, 5)))
                rows = await asyncio.to_thread(self.service.pending, group_id, read_limit)
                if not rows:
                    continue
                age = now - datetime.fromisoformat(rows[0]['created_at']).timestamp()
                if len(rows) < policy['summary_min_messages'] and age < policy['summary_wait_seconds']:
                    continue
                bounded, chars = [], 0
                for row in rows:
                    size = len(row['text'])
                    if bounded and chars + size > policy['summary_input_chars']:
                        break
                    bounded.append(row)
                    chars += size
                try:
                    await self.service.apply_batch(group_id, bounded)
                except asyncio.CancelledError:
                    raise
                except WorkDeferred:
                    continue
                except Exception as exc:
                    failures = min(10, (state['failures'] if state else 0) + 1)
                    delay = min(3600, 60 * 2 ** (failures - 1))
                    with self.service.db._connect() as conn:
                        conn.execute('INSERT OR REPLACE INTO group_summary_schedule VALUES(?,?,?)',
                                     (group_id, now + delay, failures))
                    logger.warning('Group summary batch deferred for group {}: {}', group_id, type(exc).__name__)
                    continue
                with self.service.db._connect() as conn:
                    conn.execute('INSERT OR REPLACE INTO group_summary_schedule VALUES(?,?,0)',
                                 (group_id, now + policy['summary_wait_seconds']))
                applied += len(bounded)
        return applied
