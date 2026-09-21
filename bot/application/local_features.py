"""Registry for local feature calls shared by commands and conversational routing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.exception import FinishedException

from bot.application.chat_continuation import MergedEvent
from bot.services.local_skill_contract import FeatureRequest, request_from_decision, SCOPE_LABELS


FeatureHandler = Callable[[Any, Bot, Any, FeatureRequest], Awaitable[None]]
_handlers: dict[str, FeatureHandler] = {}
_SCOPE_LABELS = SCOPE_LABELS


class StaleFeatureState(RuntimeError):
    """The mutable feature changed after the model saw its state."""


class FeatureDelivery:
    """Keep sends bound to the requesting QQ event and stop stale turns."""

    def __init__(self, bot, event, *, current=lambda: True):
        while isinstance(event, MergedEvent):
            event = event.first
        self.bot, self.event, self.current = bot, event, current
        self.receipts: list[str] = []
        self.message_ids: list[str] = []

    async def send(self, message, **kwargs):
        if not self.current():
            raise FinishedException
        result = await self.bot.send(self.event, message, **kwargs)
        if not isinstance(result, dict) or not result.get("message_id"):
            raise RuntimeError("local feature delivery has no platform receipt")
        self.message_ids.append(str(result["message_id"]))
        content = Message(message)
        summary = content.extract_plain_text()
        if any(segment.type == "image" for segment in content):
            summary += "[功能图片]"
        self.receipts.append(summary or "[功能消息]")
        return result

    async def finish(self, message=None, **kwargs):
        if message is not None:
            await self.send(message, **kwargs)
        raise FinishedException


def register_local_feature(*actions: str) -> Callable[[FeatureHandler], FeatureHandler]:
    normalized = tuple(action.strip() for action in actions if action.strip())
    if not normalized:
        raise ValueError("at least one local feature action is required")

    def decorator(handler: FeatureHandler) -> FeatureHandler:
        for action in normalized:
            existing = _handlers.get(action)
            same_registration = (
                existing is not None
                and existing.__module__ == handler.__module__
                and existing.__name__ == handler.__name__
            )
            if existing is not None and existing is not handler and not same_registration:
                raise RuntimeError(f"local feature action already registered: {action}")
            _handlers[action] = handler
        return handler

    return decorator


def feature_label(request: FeatureRequest) -> str:
    labels = {
        "zhijiang_schedule": "枝江直播日程",
        "today_live": "今日直播",
        "tomorrow_live": "明日直播",
        "week_live": "本周直播",
        "denia_gallery": "达妮娅美图",
    }
    if request.action == "ranking":
        name = "集群发言排行" if request.cluster else "发言排行"
        return f"{name} {_SCOPE_LABELS.get(request.args, request.args)}"
    return labels.get(request.action, "本地功能")


async def run_feature_call(
    matcher: Any,
    bot: Bot,
    event: Any,
    request: FeatureRequest,
) -> bool:
    handler = _handlers.get(request.action)
    if handler is None:
        return False
    # The merger is not a OneBot event subclass. Feature implementations and
    # their group/permission checks need the original transport identity.
    while isinstance(event, MergedEvent):
        event = event.first
    try:
        await handler(matcher, bot, event, request)
    except FinishedException:
        return True
    return True


def registered_local_features() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


__all__ = [
    "FeatureRequest",
    "StaleFeatureState",
    "feature_label",
    "register_local_feature",
    "registered_local_features",
    "request_from_decision",
    "run_feature_call",
]
