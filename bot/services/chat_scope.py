"""Conversation-scope helpers shared by the chat adapter and services."""
from __future__ import annotations

from typing import Any


PRIVATE_CONTEXT_GROUP_ID = 0


def is_private_message(event: Any) -> bool:
    return str(getattr(event, "message_type", "") or "") == "private"


def context_group_id(event: Any) -> int:
    if is_private_message(event):
        return PRIVATE_CONTEXT_GROUP_ID
    return int(event.group_id)


def dispatch_scope_id(event: Any) -> int:
    """Serialize each private user independently without colliding with groups."""

    if is_private_message(event):
        return -abs(int(event.user_id))
    return int(event.group_id)
