"""Bounded per-group chat tasks so slow chat/TTS never holds up other matchers."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import Context, copy_context
from dataclasses import dataclass

from nonebot import logger


@dataclass(frozen=True)
class ChatJob:
    action: Callable[[], Awaitable[None]]
    request_id: str
    current: Callable[[], bool] | None
    context: Context


class ChatDispatcher:
    def __init__(self, limit: int = 32, waiting_limit: int = 2) -> None:
        self.limit = limit
        self.waiting_limit = waiting_limit
        self.tasks: dict[int, asyncio.Task] = {}
        self.pending: dict[int, deque[ChatJob]] = {}
        self.request_ids: set[str] = set()
        self.closing = False

    @staticmethod
    def trace(group_id: int, request_id: str, stage: str, detail: str = "") -> None:
        logger.info("Chat trace group={} request_id={} stage={} detail={}",
                    group_id, request_id, stage, detail)

    def submit(self, group_id: int, action: Callable[[], Awaitable[None]], *,
               proactive: bool = False, request_id: str = "",
               current: Callable[[], bool] | None = None) -> bool:
        self.trace(group_id, request_id, "received", "proactive" if proactive else "call")
        if self.closing or (request_id and request_id in self.request_ids):
            self.trace(group_id, request_id, "dropped", "shutdown_or_duplicate")
            return False
        job = ChatJob(action, request_id, current, copy_context())
        if group_id in self.tasks:
            queue = self.pending[group_id]
            if proactive or len(queue) >= self.waiting_limit:
                self.trace(group_id, request_id, "dropped", "proactive_busy" if proactive else "queue_full")
                return False
            queue.append(job)
            if request_id:
                self.request_ids.add(request_id)
            self.trace(group_id, request_id, "queued", f"position={len(queue)}")
            return True
        if len(self.tasks) >= self.limit:
            self.trace(group_id, request_id, "dropped", "global_limit")
            return False
        self.pending[group_id] = deque()
        if request_id:
            self.request_ids.add(request_id)
        task = asyncio.create_task(self._run(group_id, job))
        self.tasks[group_id] = task
        return True

    async def _run(self, group_id: int, job: ChatJob) -> None:
        try:
            while True:
                try:
                    if job.current is not None and not job.current():
                        self.trace(group_id, job.request_id, "dropped", "stale_context")
                    else:
                        self.trace(group_id, job.request_id, "started")
                        # A worker drains several matcher invocations. Restore each
                        # invocation's event/bot/pacing context, not the first one's.
                        await asyncio.create_task(job.context.run(job.action), context=job.context)
                        self.trace(group_id, job.request_id, "finished")
                except asyncio.CancelledError:
                    self.trace(group_id, job.request_id, "cancelled", "shutdown")
                    raise
                except Exception as exc:
                    self.trace(group_id, job.request_id, "failed", type(exc).__name__)
                finally:
                    self.request_ids.discard(job.request_id)
                if not self.pending[group_id]:
                    break
                job = self.pending[group_id].popleft()
        finally:
            for waiting in self.pending.pop(group_id, ()):
                self.request_ids.discard(waiting.request_id)
                self.trace(group_id, waiting.request_id, "cancelled", "shutdown")
            self.tasks.pop(group_id, None)

    async def close(self) -> None:
        self.closing = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first step never enters _run's finally.
        self.tasks.clear()
        self.pending.clear()
        self.request_ids.clear()


dispatcher = ChatDispatcher()
