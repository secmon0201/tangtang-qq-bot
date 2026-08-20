"""Transport-level command helpers without matcher registration side effects."""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent

from bot.config import settings


def user_id(event: MessageEvent) -> int:
    return int(event.user_id)


def current_group(event: MessageEvent) -> int | None:
    return int(event.group_id) if isinstance(event, GroupMessageEvent) else None


def is_operator(event: MessageEvent) -> bool:
    return user_id(event) in settings.operator_ids


def text_arg(args: object) -> str:
    return args.extract_plain_text().strip()  # type: ignore[attr-defined]


def valid_qq_id(value: str) -> int | None:
    if not value.isdigit():
        return None
    value_as_int = int(value)
    return value_as_int if value_as_int > 0 else None


def percent_value(value: str, maximum: float) -> float | None:
    raw = value.strip().removesuffix("%")
    try:
        result = float(raw)
    except ValueError:
        return None
    if result > 1:
        result /= 100
    return result if 0 <= result <= maximum else None


def bounded_integer(value: str, minimum: int, maximum: int) -> int | None:
    if not value.isdigit():
        return None
    result = int(value)
    return result if minimum <= result <= maximum else None


__all__ = [
    "bounded_integer",
    "current_group",
    "is_operator",
    "percent_value",
    "text_arg",
    "user_id",
    "valid_qq_id",
]
