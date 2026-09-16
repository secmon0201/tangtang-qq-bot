"""Bounded timer orchestration over the existing chat dispatcher and service."""
from __future__ import annotations

import asyncio
import random
import time
from contextvars import Context, copy_context
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from nonebot import logger

from bot.config import ROOT, settings
from bot.services.pacing import passive_response_for
from bot.services.proactive_policy import ProactiveTurn, ordinary_text, proactive_turn_for
from bot.services.proactive_store import ProactiveStore


@lru_cache(maxsize=1)
def proactive_store() -> ProactiveStore:
    return ProactiveStore(ROOT / "data" / "tangtang" / "proactive.db", settings.timezone)


@dataclass(frozen=True)
class PendingChat:
    bot: Any
    event: Any
    config: Any
    persona: Any
    selection: tuple[str, int, int]
    execution_context: Context


class ProactiveCoordinator:
    def __init__(self, store: ProactiveStore, service, dispatcher, personas, *,
                 enabled: Callable[[int], bool], connected: Callable[[Any], bool],
                 groups: Callable[[], tuple[int, ...]], clock=time.time, draw=random.random) -> None:
        self.store, self.service, self.dispatcher, self.personas = store, service, dispatcher, personas
        self.enabled, self.connected, self.groups = enabled, connected, groups
        self.clock, self.draw = clock, draw
        self.pending: dict[int, PendingChat] = {}
        self.rotation = 0

    def observe(self, bot, event, config) -> None:
        group_id = int(event.group_id)
        timestamp = min(self.clock(), float(event.time))
        if self.clock() - timestamp > 90:
            return
        text = ordinary_text(event.get_plaintext())
        if not text or not self.service.proactive_text_allowed(text):
            return
        if not self.store.observe(group_id, int(event.user_id), str(event.message_id), text, timestamp):
            return
        self.pending[group_id] = PendingChat(bot, event, config,
            self.personas.snapshot(event, config.model, True), self.store.selection(group_id), copy_context())

    def current(self, pending: PendingChat) -> bool:
        now = self.clock()
        return (self.enabled(int(pending.event.group_id)) and self.connected(pending.bot)
                and now - float(pending.event.time) <= 120
                and self.personas.current(pending.persona)
                and self.store.current(int(pending.event.group_id), pending.selection, now))

    def low_groups(self) -> tuple[int, ...]:
        return tuple(g for g in self.groups() if self.enabled(g) and self.store.selection(g)[0] == "low_traffic_v1")

    async def execute(self, pending: PendingChat) -> None:
        group_id, request_id = int(pending.event.group_id), pending.persona.request_id
        turn = ProactiveTurn(pending.selection[0],
            admit=lambda: self.current(pending) and self.store.admit(group_id, request_id, pending.selection, self.clock(), self.low_groups()),
            current=lambda: self.current(pending),
            outcome=lambda event, detail: self.store.outcome(request_id, event, detail))
        try:
            with proactive_turn_for(turn), passive_response_for(pending.event):
                await self.service.handle_proactive(pending.bot, pending.event, pending.config, context=pending.persona)
        except asyncio.CancelledError:
            self.store.outcome(request_id, "cancelled", "shutdown")
            raise
        except Exception:
            self.store.outcome(request_id, "error", "dispatcher_failure")
            raise
        finally:
            self.store.finish(request_id)

    def tick(self) -> None:
        groups = sorted(self.pending)
        if not groups:
            return
        offset = self.rotation % len(groups)
        self.rotation += 1
        for group_id in groups[offset:] + groups[:offset]:
            pending = self.pending[group_id]
            if not self.current(pending):
                self.pending.pop(group_id, None)
                continue
            if group_id in self.dispatcher.tasks or self.dispatcher.closing or len(self.dispatcher.tasks) >= self.dispatcher.limit:
                continue
            if self.store.candidate(group_id, self.clock(), self.draw(),
                                    self.service.proactive_last_attempt(group_id), self.low_groups()):
                pending.execution_context.run(self.dispatcher.submit, group_id,
                    lambda p=pending: self.execute(p), proactive=True,
                    request_id=pending.persona.request_id, current=lambda p=pending: self.current(p))

    async def run(self) -> None:
        self.store.recover_interrupted()
        logger.info("Proactive scheduler started: fixed strategies, 15-second ticks")
        while True:
            await asyncio.sleep(15)
            try:
                self.tick()
                self.store.heartbeat(self.clock())
            except Exception:
                logger.exception("Proactive timer tick failed; other features remain available")
