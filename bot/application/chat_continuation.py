"""Bounded reply windows and burst merging over the shared chat dispatcher."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from bot.config import ROOT, settings
from bot.services.continuation_policy import (
    ContinuationConfig, ContinuationStore, ContinuationTurn, ConversationWindow,
    continuation_turn, continuation_turn_for,
)
from bot.services.pacing import passive_response_for


@lru_cache(maxsize=1)
def continuation_store() -> ContinuationStore:
    return ContinuationStore(ROOT / "data" / "personas" / "continuation.db", settings.timezone)


def continuation_config(store) -> ContinuationConfig:
    defaults = ContinuationConfig()
    fields = {
        "idle_seconds": "idle_seconds", "hard_seconds": "hard_seconds",
        "max_attempts": "max_attempts", "max_silences": "silence_limit",
        "group_daily": "group_daily_limit", "global_daily": "global_daily_limit",
        "debounce_seconds": "debounce_seconds", "max_debounce_seconds": "max_debounce_seconds",
    }
    values = {}
    for field_name, option_name in fields.items():
        default = getattr(defaults, field_name)
        try:
            values[field_name] = max(0, type(default)(store.option("continuation_" + option_name, default)))
        except (TypeError, ValueError):
            values[field_name] = default
    return ContinuationConfig(**values)


def has_other_addressee(event: Any) -> bool:
    original = getattr(event, "original_message", event.message)
    if any(segment.type == "at" and str(segment.data.get("qq")) != str(event.self_id)
           for segment in original):
        return True
    reply = getattr(event, "reply", None)
    if reply is not None:
        return str(getattr(getattr(reply, "sender", None), "user_id", "")) != str(event.self_id)
    # An unresolved quote must not be assumed to address the bot.
    return any(segment.type == "reply" for segment in original)


class MergedEvent:
    """Preserve the first event identity and media while appending same-user input."""

    def __init__(self, events: list[Any]) -> None:
        self.first = events[0]
        self.source_message_ids = tuple(str(event.message_id) for event in events)
        self.message = Message()
        self.original_message = Message()
        for index, event in enumerate(events):
            if index:
                self.message += "\n"
                self.original_message += "\n"
            self.message += event.message
            self.original_message += getattr(event, "original_message", event.message)

    def __getattr__(self, key: str) -> Any:
        return getattr(self.first, key)

    def get_plaintext(self) -> str:
        return self.message.extract_plain_text()

    def get_message(self) -> Message:
        return self.message


@dataclass
class PendingBurst:
    bot: Any
    config: Any
    context: Any
    window: ConversationWindow | None
    explicit: bool
    first_at: float
    last_at: float
    events: list[Any] = field(default_factory=list)
    task: asyncio.Task | None = None


class ContinuationCoordinator:
    def __init__(self, service, dispatcher, personas, *,
                 enabled: Callable[[int], bool], connected: Callable[[Any], bool],
                 config: Callable[[], ContinuationConfig] = ContinuationConfig,
                 store: Callable[[], ContinuationStore] = continuation_store,
                 clock=time.time, sleeper=asyncio.sleep) -> None:
        self.service, self.dispatcher, self.personas = service, dispatcher, personas
        self.enabled, self.connected = enabled, connected
        self.config, self.store, self.clock, self.sleep = config, store, clock, sleeper
        self.windows: dict[int, ConversationWindow] = {}
        self.pending: dict[tuple[int, int], PendingBurst] = {}
        self.dispatched: dict[str, PendingBurst] = {}
        self.closing = False

    def _window(self, group_id: int) -> ConversationWindow | None:
        window = self.windows.get(group_id)
        if window is not None and (self.closing or not self.enabled(group_id)
                or not window.current(self.clock(), self.config())
                or not self.personas.current(window.context)):
            self.windows.pop(group_id, None)
            return None
        return window

    def has_active_group(self, group_id: int) -> bool:
        for request_id, burst in tuple(self.dispatched.items()):
            if not self._burst_current(burst):
                self.dispatched.pop(request_id, None)
        return (self._window(group_id) is not None
                or any(key[0] == group_id for key in self.pending)
                or any(burst.context.group_id == group_id for burst in self.dispatched.values()))

    def eligible(self, event: Any) -> bool:
        group, user = int(event.group_id), int(event.user_id)
        if group not in self.windows and (group, user) not in self.pending:
            return False
        if self.closing or has_other_addressee(event):
            return False
        pending = self.pending.get((group, user))
        if pending and self._burst_current(pending):
            return True
        if not self.enabled(group):
            return False
        window = self._window(group)
        return (window is not None and window.context.user_id == user
                and window.attempts < self.config().max_attempts)

    def outcome(self, context, event_kind: str, detail: str = "") -> None:
        # Automatic delivery refreshes the existing window in its own hook.
        if continuation_turn() is not None or event_kind not in {"reply", "canned", "proactive_reply"}:
            return
        if not self.closing and self.enabled(context.group_id) and self.personas.current(context):
            now = self.clock()
            self.windows[context.group_id] = ConversationWindow(context, now, now)

    def offer(self, bot, event, config, context, *, explicit: bool) -> bool:
        """Return immediately; matcher work never waits for debounce or a model."""
        if self.closing or self.dispatcher.closing or not self.connected(bot):
            return False
        if not explicit and not self.eligible(event):
            return False
        now = self.clock()
        key = (int(event.group_id), int(event.user_id))
        # A replayed member of an earlier burst must not absorb new text and
        # make the entire fresh burst fail the final atomic replay guard.
        message_id = str(event.message_id)
        if (self.personas.store.request_claimed(context.request_id, now)
                or any(burst.context.group_id == key[0]
                       and any(str(item.message_id) == message_id for item in burst.events)
                       for burst in (*self.pending.values(), *self.dispatched.values()))):
            self.dispatcher.trace(key[0], context.request_id, "dropped", "burst_replay")
            return True
        pending = self.pending.get(key)
        if pending and (not self._burst_current(pending)
                        or pending.context.persona != context.persona
                        or pending.context.selection_revision != context.selection_revision):
            self._cancel(key)
            pending = None
        if pending:
            limits = self.config()
            if (len(pending.events) >= limits.max_messages
                    or sum(len(item.get_plaintext()) for item in pending.events) + len(event.get_plaintext()) > limits.max_chars):
                self.dispatcher.trace(key[0], context.request_id, "dropped", "burst_limit")
                return True
            pending.events.append(event)
            pending.last_at = now
            pending.explicit = pending.explicit or explicit
            return True
        if len(self.pending) >= self.config().max_pending:
            self.dispatcher.trace(key[0], context.request_id, "dropped", "debounce_capacity")
            return False
        pending = PendingBurst(bot, config, context, self._window(key[0]), explicit, now, now, [event])
        self.pending[key] = pending
        pending.task = asyncio.create_task(self._wait_and_submit(key, pending))
        return True

    def _burst_current(self, burst: PendingBurst) -> bool:
        if (self.closing or not self.connected(burst.bot) or not self.personas.current(burst.context)
                or self.clock() - float(burst.events[0].time) > 120):
            return False
        return burst.explicit or (self.enabled(burst.context.group_id)
                and self._window(burst.context.group_id) is burst.window)

    async def _wait_and_submit(self, key: tuple[int, int], burst: PendingBurst) -> None:
        try:
            while self._burst_current(burst):
                config = self.config()
                deadline = min(burst.last_at + config.debounce_seconds,
                               burst.first_at + config.max_debounce_seconds)
                remaining = deadline - self.clock()
                if remaining <= 0:
                    event = MergedEvent(burst.events)
                    if self.dispatcher.submit(key[0], lambda: self._dispatch_action(burst, event),
                            request_id=burst.context.request_id, current=lambda: self._burst_current(burst)):
                        self.dispatched[burst.context.request_id] = burst
                    return
                await self.sleep(remaining)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Continuation burst failed before dispatch")
        finally:
            if self.pending.get(key) is burst:
                self.pending.pop(key, None)

    async def _dispatch_action(self, burst: PendingBurst, event: MergedEvent) -> None:
        try:
            await self._execute(burst, event)
        finally:
            self.dispatched.pop(burst.context.request_id, None)

    async def _execute(self, burst: PendingBurst, event: MergedEvent) -> None:
        if burst.explicit:
            with passive_response_for(event):
                await self.service.handle(burst.bot, event, burst.config, context=burst.context)
            return
        window, admitted, terminal = burst.window, False, False

        def admit() -> bool:
            nonlocal admitted
            config = self.config()
            if admitted or not self._burst_current(burst) or window.attempts >= config.max_attempts:
                return False
            if not self.store().claim(event.group_id, burst.context.request_id, self.clock(), config):
                return False
            admitted = True
            window.attempts += 1
            return True

        def outcome(kind: str, detail: str) -> None:
            nonlocal terminal
            if not admitted or terminal:
                return
            if kind in {"reply", "canned", "proactive_reply"}:
                terminal = True
                if self._burst_current(burst):
                    window.delivered_at = self.clock()
                    window.silences = 0
                self.store().outcome(burst.context.request_id, "delivered")
            elif kind == "silent":
                terminal = True
                window.silences += 1
                self.store().outcome(burst.context.request_id, "silent")

        turn = ContinuationTurn(admit, lambda: self._burst_current(burst), outcome)
        try:
            with continuation_turn_for(turn), passive_response_for(event):
                await self.service.handle_continuation(burst.bot, event, burst.config, context=burst.context)
        finally:
            if admitted and not terminal:
                self.store().outcome(burst.context.request_id, "cancelled_or_failed")
            if window.attempts >= self.config().max_attempts or window.silences >= self.config().max_silences:
                if self.windows.get(event.group_id) is window:
                    self.windows.pop(event.group_id, None)

    def _cancel(self, key: tuple[int, int]) -> None:
        burst = self.pending.pop(key, None)
        if burst and burst.task:
            burst.task.cancel()

    async def close(self) -> None:
        self.closing = True
        tasks = tuple(burst.task for burst in self.pending.values() if burst.task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.dispatched.clear()
        self.windows.clear()
