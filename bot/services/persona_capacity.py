"""Shared endpoint capacity with a reserved foreground lane and bounded waits."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from weakref import WeakKeyDictionary
from bot.services.pacing import assert_outbound_current


background_request = ContextVar('persona_background_request', default=False)
_loops = WeakKeyDictionary()


class EndpointCapacity:
    def __init__(self, total=4, background=1):
        self.total = max(1, total)
        self.background_limit = min(max(1, background), max(1, total - 1))
        self.active = self.background_active = self.foreground_waiting = 0
        self.condition = asyncio.Condition()

    @asynccontextmanager
    async def acquire(self, background=False):
        async with self.condition:
            if not background:
                self.foreground_waiting += 1
            try:
                await self.condition.wait_for(lambda: self.active < self.total and (
                    not background or (self.background_active < self.background_limit and not self.foreground_waiting)))
                self.active += 1
                self.background_active += int(background)
            finally:
                if not background:
                    self.foreground_waiting -= 1
                self.condition.notify_all()
        try:
            yield
        finally:
            async with self.condition:
                self.active -= 1
                self.background_active -= int(background)
                self.condition.notify_all()


async def provider_post(client, endpoint, **kwargs):
    capacities = _loops.setdefault(asyncio.get_running_loop(), {})
    capacity = capacities.setdefault(endpoint, EndpointCapacity())
    async with asyncio.timeout(45):
        async with capacity.acquire(background_request.get()):
            assert_outbound_current()
            return await client.post(endpoint, **kwargs)
