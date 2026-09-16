from __future__ import annotations

import asyncio
import random
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic, time
from typing import Any, Iterator, Callable

from bot.config import settings
from bot.services.game_api_gate import GAME_COMMAND_RE


_api_lock = asyncio.Lock()
_last_api_call = 0.0
_api_call_is_paced: ContextVar[bool] = ContextVar("api_call_is_paced", default=False)
_outbound_guard: ContextVar[Callable[[], bool] | None] = ContextVar("outbound_guard", default=None)
_outbound_response: ContextVar["OutboundResponse | None"] = ContextVar(
    "outbound_response", default=None
)

OUTBOUND_RESPONSE_ACTIONS = frozenset(
    {
        "send_msg",
        "send_group_msg",
        "send_private_msg",
        "send_group_forward_msg",
        "send_private_forward_msg",
        "set_msg_emoji_like",
    }
)
PASSIVE_SEND_MAX_AGE_SECONDS = 120


class OutboundCancelled(RuntimeError):
    """The originating request became stale before the actual platform call."""


@contextmanager
def guard_outbound_for(predicate: Callable[[], bool]) -> Iterator[None]:
    token = _outbound_guard.set(predicate)
    try:
        yield
    finally:
        _outbound_guard.reset(token)


def assert_outbound_current() -> None:
    predicate = _outbound_guard.get()
    if predicate is not None and not predicate():
        raise OutboundCancelled("request persona or settings changed")


@dataclass(frozen=True, slots=True)
class OutboundResponse:
    kind: str
    event: Any | None = None


def _event_text(event: Any) -> str:
    try:
        return str(event.get_plaintext()).strip()
    except (AttributeError, TypeError):
        return ""


def _current_response() -> OutboundResponse | None:
    response = _outbound_response.get()
    if response is not None:
        return response
    try:
        from nonebot.matcher import current_event

        event = current_event.get()
    except (ImportError, LookupError):
        return None
    text = _event_text(event)
    return OutboundResponse(
        "command" if text.startswith(settings.command_prefix) or GAME_COMMAND_RE.match(text) else "passive",
        event,
    )


def _is_stale_passive_response(response: OutboundResponse) -> bool:
    if response.kind != "passive" or response.event is None:
        return False
    timestamp = int(getattr(response.event, "time", 0) or 0)
    return timestamp > 0 and time() - timestamp > PASSIVE_SEND_MAX_AGE_SECONDS


@contextmanager
def passive_response_for(event: Any) -> Iterator[None]:
    """Mark an API call outside a matcher as a passive interaction response."""
    token = _outbound_response.set(OutboundResponse("passive", event))
    try:
        yield
    finally:
        _outbound_response.reset(token)


async def prepare_outbound_response(action: str) -> bool:
    """Apply response-specific delay immediately before a visible OneBot action."""
    if action not in OUTBOUND_RESPONSE_ACTIONS:
        return True
    response = _current_response()
    if response is None:
        # Scheduled notices and completion notifications keep only API pacing.
        return True
    if _is_stale_passive_response(response):
        return False
    if response.kind == "command":
        delay = random.uniform(
            settings.command_response_delay_min_seconds,
            settings.command_response_delay_max_seconds,
        )
    else:
        delay = random.uniform(
            settings.response_delay_min_seconds,
            settings.response_delay_max_seconds,
        )
    await asyncio.sleep(delay)
    return True


async def wait_for_human_turn() -> None:
    """Compatibility helper for callers that need a passive interaction delay."""
    await asyncio.sleep(
        random.uniform(
            settings.response_delay_min_seconds,
            settings.response_delay_max_seconds,
        )
    )


async def wait_for_api_turn() -> None:
    """Serialize OneBot calls and leave a configured gap between requests."""
    global _last_api_call

    async with _api_lock:
        remaining = settings.onebot_api_min_interval_seconds - (monotonic() - _last_api_call)
        if remaining > 0:
            await asyncio.sleep(remaining)
        _last_api_call = monotonic()


async def paced_call_api(bot: Any, action: str, **params: Any) -> Any:
    """Legacy OneBot transport primitive; QQ business code uses qq_platform."""
    if not await prepare_outbound_response(action):
        return None
    await wait_for_api_turn()
    if action in OUTBOUND_RESPONSE_ACTIONS:
        assert_outbound_current()
    token = _api_call_is_paced.set(True)
    try:
        return await bot.call_api(action, **params)
    finally:
        _api_call_is_paced.reset(token)


def api_call_is_paced() -> bool:
    """Whether the current API call already passed through ``paced_call_api``."""
    return _api_call_is_paced.get()
