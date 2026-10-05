"""Visible request copies; this projection never changes the model's wire body."""
from __future__ import annotations

from typing import Any


SECRET_FIELDS = {"token", "access_token", "authorization", "password", "secret", "api_key", "access_key"}
REDACTED = "[redacted]"


def redact_payload(value: Any, *, replacement: str = REDACTED) -> Any:
    if isinstance(value, dict):
        return {key: (replacement if key.casefold() in SECRET_FIELDS else redact_payload(item, replacement=replacement))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_payload(item, replacement=replacement) for item in value]
    return value


def restore_redacted(value: Any, saved: Any) -> Any:
    """Keep an unchanged editor placeholder; empty values or removed keys clear it."""
    if isinstance(value, dict):
        old = saved if isinstance(saved, dict) else {}
        return {key: (old.get(key, "") if key.casefold() in SECRET_FIELDS and item == REDACTED
                      else restore_redacted(item, old.get(key))) for key, item in value.items()}
    if isinstance(value, list):
        old = saved if isinstance(saved, list) else []
        return [restore_redacted(item, old[index] if index < len(old) else None) for index, item in enumerate(value)]
    return value
