from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nonebot.adapters import MessageTemplate
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher, current_event, current_matcher


_EXCLUDED_PLUGIN_NAMES = frozenset({"stats"})


def quote_message(
    event: MessageEvent, message: str | Message | MessageSegment
) -> Message:
    """Prefix one OneBot response with a native reply segment."""
    if has_reply_segment(message):
        return Message(message)
    return MessageSegment.reply(event.message_id) + message


def has_reply_segment(message: str | Message | MessageSegment) -> bool:
    if isinstance(message, MessageSegment):
        return message.type == "reply"
    if isinstance(message, Message):
        return bool(message) and message[0].type == "reply"
    return False


def _is_excluded_matcher(matcher: type[Matcher]) -> bool:
    if bool(getattr(matcher, "_tangtang_skip_quote", False)):
        return True
    plugin_name = str(getattr(matcher, "plugin_name", "") or "")
    module_name = str(getattr(matcher, "module_name", "") or "")
    return (
        plugin_name in _EXCLUDED_PLUGIN_NAMES
        or plugin_name.startswith("GenshinUID")
        or "GenshinUID" in module_name
    )


def install_matcher_quote_replies() -> None:
    """Quote ordinary OneBot matcher responses without touching forwards.

    Matchers use the event stored in NoneBot's context variable, so this wraps
    only responses caused by an incoming message. Scheduled broadcasts and
    direct forward messages have no event and remain unchanged.
    """
    if getattr(Matcher, "_tangtang_quote_replies_installed", False):
        return

    original_send: Callable[..., Any] = Matcher.send.__func__

    async def quoted_send(
        cls: type[Matcher], message: str | Message | MessageSegment | MessageTemplate, **kwargs: Any
    ) -> Any:
        try:
            event = current_event.get()
        except LookupError:
            event = None
        if isinstance(message, MessageTemplate):
            message = message.format(**current_matcher.get().state)
        if (
            isinstance(event, MessageEvent)
            and not _is_excluded_matcher(cls)
            and not has_reply_segment(message)
        ):
            message = quote_message(event, message)
        return await original_send(cls, message, **kwargs)

    Matcher.send = classmethod(quoted_send)
    setattr(Matcher, "_tangtang_quote_replies_installed", True)
