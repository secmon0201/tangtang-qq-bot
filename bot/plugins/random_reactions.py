from __future__ import annotations

import random
import re
from time import time

from nonebot import logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent

from bot.config import settings
from bot.services.command_classification import is_activity_command_text
from bot.services.game_api_gate import NTE_GAME_COMMAND_RE
from bot.services.qq_platform import qq_platform
from bot.services.runtime import database, passive_settings
from bot.services.reactions import (
    RANDOM_REACTION_CLAIMED,
    random_reaction_decision,
    random_repeat_decision,
    should_log_random_reaction_decision,
)
from bot.services.triple_repeat import triple_repeat_tracker


db = database()
passive = passive_settings()
CODEX_COMMAND_RE = re.compile(r"^#\s*codex(?:\s|$)", re.IGNORECASE)
PASSIVE_EVENT_MAX_AGE_SECONDS = 120
MINI_GAME_COMMAND_RE = re.compile(
    r"^(?:[\u4e00-\u9fff]*\s*)?#\s*(?:丢给(?=\s|@|$)|猜(?:\s*.*)?|装弹\s*成语(?:\s*.*)?|(?:装填|开枪|装弹|骰子|(?:小)?游戏列表|转盘(?:总)?榜|炸弹(?:总)?榜|骰子(?:总)?榜|猜数(?:总)?榜|"
    r"俄罗斯转盘(?:总)?榜单|定时炸弹(?:总)?榜单|幸运骰局(?:总)?榜单|猜数字(?:总)?榜单|游戏(?:开|关|状态)|游戏禁言(?:开|关|状态)|清游|确认|取消)(?:\s|$))"
)
RETIRED_DICE_COMMAND_RE = re.compile(r"^#\s*(?:重投|不投)(?:\s|$)")


def automation_is_paused() -> bool:
    """Read the immediate runtime pause without making an absent DB fatal."""

    try:
        return db.passive_settings().get("automation_pause_active", "false") == "true"
    except (AttributeError, OSError):
        return False


def is_passive_reaction_event(event: MessageEvent) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and not automation_is_paused()
        and not event.is_tome()
        and passive.is_group_enabled(int(event.group_id))
        and not is_stale_passive_event(event)
        and not event.get_plaintext().lstrip().startswith(settings.command_prefix)
        and not NTE_GAME_COMMAND_RE.match(event.get_plaintext().strip())
        and not is_activity_command_text(event.get_plaintext())
        and not CODEX_COMMAND_RE.match(event.get_plaintext().strip())
        and not MINI_GAME_COMMAND_RE.match(event.get_plaintext().strip())
        and not RETIRED_DICE_COMMAND_RE.match(event.get_plaintext().strip())
    )


def is_stale_passive_event(event: GroupMessageEvent, *, now: float | None = None) -> bool:
    """Ignore delayed OneBot deliveries so passive replies never target old chat."""
    timestamp = int(getattr(event, "time", 0) or 0)
    return timestamp > 0 and (time() if now is None else now) - timestamp > PASSIVE_EVENT_MAX_AGE_SECONDS


def is_reaction_filtered(event: GroupMessageEvent) -> bool:
    return db.passive_filter_contains(int(event.user_id))


def repeatable_text(event: GroupMessageEvent) -> str | None:
    if not event.message or any(segment.type != "text" for segment in event.message):
        return None
    text = event.get_plaintext().strip()
    return text if 1 <= len(text) <= 80 else None


def should_triple_repeat(probability: float, random_value: float) -> bool:
    """Return True when a triple-repeat trigger wins the configured probability."""
    return float(random_value) < float(probability)


# This matcher only receives ordinary non-command group messages. Blocking it
# prevents passive reactions from competing with other passive listeners.
random_group_reaction = on_message(rule=is_passive_reaction_event, priority=0, block=True)


@random_group_reaction.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if str(event.user_id) == str(bot.self_id):
        await random_group_reaction.finish()
    current = passive.for_group(int(event.group_id))
    filtered = is_reaction_filtered(event)
    text = None if filtered else repeatable_text(event)
    if not filtered and settings.random_reaction_enabled:
        decision = await random_reaction_decision(
            int(event.group_id),
            probability=current.reaction_probability,
            cooldown_seconds=current.reaction_cooldown_seconds,
            enabled=settings.random_reaction_enabled,
        )
        if decision != RANDOM_REACTION_CLAIMED:
            if should_log_random_reaction_decision(int(event.group_id), decision):
                logger.warning(
                    "Passive group reaction skipped (group_id={}, message_id={}, reason={})",
                    event.group_id,
                    event.message_id,
                    decision,
                )
        else:
            emoji_id = random.choice(settings.random_reaction_emoji_ids)
            try:
                await qq_platform(bot).add_reaction(int(event.message_id), emoji_id)
            except Exception as exc:
                logger.warning(
                    "Passive group reaction failed (group_id={}, message_id={}, emoji_id={}): {}",
                    event.group_id,
                    event.message_id,
                    emoji_id,
                    exc,
                )
            else:
                logger.warning(
                    "Passive group reaction sent (group_id={}, message_id={}, emoji_id={})",
                    event.group_id,
                    event.message_id,
                    emoji_id,
                )

    triple_repeat_triggered = bool(
        current.triple_repeat_enabled
        and text is not None
        and triple_repeat_tracker.observe(int(event.group_id), text)
    )
    triple_repeat_sent = False
    if triple_repeat_triggered:
        if not should_triple_repeat(current.triple_repeat_probability, random.random()):
            logger.warning(
                "Triple repeat skipped (group_id={}, message_id={}, probability={})",
                event.group_id,
                event.message_id,
                current.triple_repeat_probability,
            )
        else:
            try:
                await qq_platform(bot).send_group_message(int(event.group_id), text)
            except Exception as exc:
                logger.warning(
                    "Triple repeat failed (group_id={}, message_id={}): {}",
                    event.group_id,
                    event.message_id,
                    exc,
                )
            else:
                triple_repeat_sent = True
                logger.warning(
                    "Triple repeat sent (group_id={}, message_id={})",
                    event.group_id,
                    event.message_id,
                )

    if settings.random_repeat_enabled:
        repeat_decision = random_repeat_decision(
            db,
            int(event.group_id),
            probability=current.repeat_probability,
            cooldown_seconds=current.repeat_cooldown_seconds,
            message_interval=current.repeat_message_interval,
            # The triple reply is separate from random-repeat cooldown. Count this
            # message for the random interval but avoid a duplicate bot message.
            repeatable=text is not None and not triple_repeat_sent,
        )
        if repeat_decision == RANDOM_REACTION_CLAIMED:
            try:
                await qq_platform(bot).send_group_message(int(event.group_id), text)
            except Exception as exc:
                logger.warning(
                    "Passive group repeat failed (group_id={}, message_id={}): {}",
                    event.group_id,
                    event.message_id,
                    exc,
                )
            else:
                logger.warning(
                    "Passive group repeat sent (group_id={}, message_id={})",
                    event.group_id,
                    event.message_id,
                )
        elif repeat_decision not in {"not_repeatable", "message_interval"}:
            log_key = f"repeat:{repeat_decision}"
            if should_log_random_reaction_decision(int(event.group_id), log_key):
                logger.warning(
                    "Passive group repeat skipped (group_id={}, message_id={}, reason={})",
                    event.group_id,
                    event.message_id,
                    repeat_decision,
                )
    await random_group_reaction.finish()
