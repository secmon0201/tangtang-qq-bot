"""Registry for local feature calls shared by commands and conversational routing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import FinishedException

from bot.services.tangtang_features import FeatureDecision


@dataclass(frozen=True, slots=True)
class FeatureRequest:
    action: str
    args: str = ""
    a_coast: bool = False


FeatureHandler = Callable[[Any, Bot, Any, FeatureRequest], Awaitable[None]]
_handlers: dict[str, FeatureHandler] = {}
_SCOPE_LABELS = {"day": "日", "week": "周", "month": "月", "total": "总"}


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


def request_from_decision(decision: FeatureDecision) -> FeatureRequest:
    if decision.action in {"group_ranking", "a_coast_ranking"}:
        return FeatureRequest(
            action="ranking",
            args=_SCOPE_LABELS.get(decision.scope, decision.scope),
            a_coast=decision.a_coast or decision.action == "a_coast_ranking",
        )
    return FeatureRequest(action=decision.action, args="", a_coast=False)


def feature_label(request: FeatureRequest) -> str:
    labels = {
        "zhijiang_schedule": "枝江直播日程",
        "today_live": "今日直播",
        "tomorrow_live": "明日直播",
        "week_live": "本周直播",
        "activity_hall": "活动大厅",
    }
    if request.action == "ranking":
        name = "A海岸发言排行" if request.a_coast else "发言排行"
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
    try:
        await handler(matcher, bot, event, request)
    except FinishedException:
        return True
    return True


def registered_local_features() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


__all__ = [
    "FeatureRequest",
    "feature_label",
    "register_local_feature",
    "registered_local_features",
    "request_from_decision",
    "run_feature_call",
]
