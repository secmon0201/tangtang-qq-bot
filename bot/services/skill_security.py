"""Skill permission checks and secret redaction for skill telemetry."""
from __future__ import annotations

import re
from collections.abc import Iterable


_BEARER = re.compile(r"(?i)\b(bearer|token|password|secret|api[_-]?key)\b\s*[:=]?\s*[^\s,;]+")
_SK_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_GH_KEY = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")

ROLES = frozenset({"member", "admin", "super_admin"})


def redact(text: object, *, maximum: int = 1000) -> str:
    """Strip credential-like values before they reach logs or the ledger."""

    value = str(text or "")
    value = _BEARER.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    value = _SK_KEY.sub("<redacted>", value)
    value = _GH_KEY.sub("<redacted>", value)
    return value[: max(0, int(maximum))]


def role_allows(
    required_role: str,
    *,
    is_admin: bool = False,
    is_super_admin: bool = False,
) -> bool:
    """Return whether the current speaker satisfies the skill requirement."""

    role = str(required_role or "member")
    if role not in ROLES:
        return False
    if role == "member":
        return True
    if role == "admin":
        return bool(is_admin or is_super_admin)
    return bool(is_super_admin)


def blocked_skills(
    skills: Iterable[tuple[str, str]],
    *,
    is_admin: bool = False,
    is_super_admin: bool = False,
) -> list[str]:
    return [
        skill_id
        for skill_id, required_role in skills
        if not role_allows(
            required_role, is_admin=is_admin, is_super_admin=is_super_admin
        )
    ]
