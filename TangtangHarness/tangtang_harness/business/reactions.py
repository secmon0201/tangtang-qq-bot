from __future__ import annotations

import asyncio
import random
from time import monotonic, time
from typing import Any
from typing import Final

from tangtang_harness.business.config import settings


_claim_lock = asyncio.Lock()
_next_reaction_at: dict[int, float] = {}
_next_decision_log_at: dict[tuple[int, str], float] = {}

RANDOM_REACTION_CLAIMED: Final = "claimed"
RANDOM_REACTION_DISABLED: Final = "disabled"
RANDOM_REACTION_COOLDOWN: Final = "cooldown"
RANDOM_REACTION_PROBABILITY: Final = "probability"


def random_reactions_enabled_for_group(group_id: int) -> bool:
    return settings.random_reaction_enabled and int(group_id) in settings.random_reaction_group_ids


def passive_features_enabled_for_group(group_id: int) -> bool:
    return int(group_id) in settings.random_reaction_group_ids and (
        settings.random_reaction_enabled or settings.random_repeat_enabled
    )


async def random_reaction_decision(
    group_id: int,
    probability: float | None = None,
    cooldown_seconds: int | None = None,
    enabled: bool | None = None,
) -> str:
    """Return the reason a passive group message will or will not be reacted to."""
    if not (random_reactions_enabled_for_group(group_id) if enabled is None else enabled):
        return RANDOM_REACTION_DISABLED

    async with _claim_lock:
        now = monotonic()
        if _next_reaction_at.get(int(group_id), 0.0) > now:
            return RANDOM_REACTION_COOLDOWN
        selected_probability = settings.random_reaction_probability if probability is None else probability
        selected_cooldown = (
            settings.random_reaction_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        )
        if random.random() >= selected_probability:
            return RANDOM_REACTION_PROBABILITY
        _next_reaction_at[int(group_id)] = now + selected_cooldown
        return RANDOM_REACTION_CLAIMED


async def claim_random_reaction(group_id: int) -> bool:
    """Reserve a passive reaction slot when the group, probability, and cooldown allow it."""
    return await random_reaction_decision(group_id) == RANDOM_REACTION_CLAIMED


def should_log_random_reaction_decision(group_id: int, decision: str) -> bool:
    """Keep passive-reaction diagnostics useful without logging every group message."""
    key = (int(group_id), decision)
    now = monotonic()
    if _next_decision_log_at.get(key, 0.0) > now:
        return False
    _next_decision_log_at[key] = now + 30
    return True


def random_repeat_decision(
    database: Any,
    group_id: int,
    probability: float,
    cooldown_seconds: int,
    message_interval: int,
    repeatable: bool,
) -> str:
    return database.claim_random_repeat(
        group_id=int(group_id),
        now=time(),
        probability=probability,
        cooldown_seconds=cooldown_seconds,
        message_interval=message_interval,
        random_value=random.random(),
        repeatable=repeatable,
    )
