"""Bounded per-group chat tasks so slow chat/TTS never holds up other matchers."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from nonebot import logger


class ChatDispatcher:
    def __init__(self, limit: int = 32) -> None:
        self.limit = limit
        self.tasks: dict[int, asyncio.Task] = {}

    def submit(self, group_id: int, action: Callable[[], Awaitable[None]]) -> bool:
        if group_id in self.tasks or len(self.tasks) >= self.limit:
            return False
        task = asyncio.create_task(action())
        self.tasks[group_id] = task
        task.add_done_callback(lambda completed: self._done(group_id, completed))
        return True

    def _done(self, group_id: int, task: asyncio.Task) -> None:
        self.tasks.pop(group_id, None)
        if not task.cancelled() and task.exception():
            logger.warning("Background chat failed: {}", type(task.exception()).__name__)

    async def close(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


dispatcher = ChatDispatcher()
