from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Iterable

from nonebot import logger, on_message
from nonebot.exception import IgnoredException
from nonebot.message import event_postprocessor, event_preprocessor
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment

from bot.config import settings
from bot.services.command_classification import (
    ACTIVITY_COMMAND_RE,
    PARTICIPATION_COMMAND_RE,
    is_activity_command_text,
    is_participation_command_text,
)
from bot.services.pacing import passive_response_for
from bot.services.qq_platform import call_qq_action
from bot.services.replies import install_matcher_quote_replies
from bot.services.roles import UserRole, is_super_admin, user_role
from bot.services.runtime import database, passive_settings, zhijiang_live_guard
from bot.services.zhijiang_live_reminders import render_live_game_reminder


install_matcher_quote_replies()

FILTER_MANAGEMENT_COMMAND_RE = re.compile(
    r"^#\s*(?:主动过滤|被动过滤|移除主动过滤|移除被动过滤|主动过滤列表|被动过滤列表)(?:\s|$)"
)


def is_managed_group(group_id: int, managed_group_ids: Iterable[int]) -> bool:
    return int(group_id) in {int(item) for item in managed_group_ids}


MINI_GAME_NON_PLAY_COMMANDS = frozenset(
    {
        "#游戏列表",
        "#小游戏列表",
        "#游戏开",
        "#游戏关",
        "#游戏状态",
        "#游戏禁言开",
        "#游戏禁言关",
        "#游戏禁言状态",
        "#总游戏开",
        "#游戏总开",
        "#总游戏关",
        "#游戏总关",
        "#总游戏状态",
        "#游戏总状态",
    }
)
MINI_GAME_COMMAND_RE = re.compile(
    r"^(?:[\u4e00-\u9fff]*\s*)?#\s*(?:丢给(?=\s|@|$)|猜(?:\s*.*)?|装弹\s*成语(?:\s*.*)?|(?:装填|开枪|装弹|骰子|(?:小)?游戏列表|转盘(?:总)?榜|炸弹(?:总)?榜|骰子(?:总)?榜|猜数(?:总)?榜|"
    r"俄罗斯转盘(?:总)?榜单|定时炸弹(?:总)?榜单|幸运骰局(?:总)?榜单|猜数字(?:总)?榜单|游戏禁言(?:开|关|状态))(?:\s|$))"
)
MINI_GAME_ADMIN_COMMAND_RE = re.compile(
    r"^#\s*(?:游戏(?:开|关|状态)|游戏禁言(?:开|关|状态)|清游|确认|取消)(?:\s|$)"
)
def is_feature_group(group_id: int, enabled_group_ids: Iterable[int]) -> bool:
    return int(group_id) in {int(item) for item in enabled_group_ids}


def is_bot_mentioned(event: MessageEvent) -> bool:
    return bool(event.is_tome())


def is_participation_command(event: MessageEvent) -> bool:
    return is_participation_command_text(event.get_plaintext())


def is_activity_command(event: MessageEvent) -> bool:
    return is_activity_command_text(event.get_plaintext())


def is_unmentioned_activity_command(
    event: MessageEvent, enabled_group_ids: Iterable[int]
) -> bool:
    if not is_activity_command(event):
        return False
    if not isinstance(event, GroupMessageEvent):
        return True
    return is_feature_group(int(event.group_id), enabled_group_ids)


def should_acknowledge_mention(event: MessageEvent) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and is_bot_mentioned(event)
        and not is_participation_command(event)
    )


def is_disabled_game_command(event: MessageEvent) -> bool:
    """Gate only the local mini-games; the GenshinUID/NTE game interface has
    its own independent gate in ``bot.plugins.game_api``."""
    text = event.get_plaintext().strip()
    command = "#" + text[1:].lstrip() if text.startswith("#") else text
    if command in MINI_GAME_NON_PLAY_COMMANDS:
        return False
    if not MINI_GAME_COMMAND_RE.match(text):
        return False
    if not isinstance(event, GroupMessageEvent):
        # Mini-games have no private-chat scope. Silently consume their
        # command-shaped messages.
        return True
    group_id = int(event.group_id)
    feature_scopes = passive_settings()
    enabled = is_feature_group(group_id, feature_scopes.groups("game"))
    global_enabled = feature_scopes.is_game_globally_enabled()
    suppressed = is_managed_group(group_id, settings.managed_group_ids) and (
        not enabled or not global_enabled
    )
    logger.info(
        "Mini-game command gate (group={}, user={}, command={!r}, enabled={}, global_enabled={}, suppressed={})",
        group_id,
        event.user_id,
        text,
        enabled,
        global_enabled,
        suppressed,
    )
    return suppressed


def live_guard_mini_game_reminder(event: MessageEvent) -> MessageSegment | None:
    """Build a notice only when the live guard, rather than a manual switch, paused games."""
    if not isinstance(event, GroupMessageEvent):
        return None
    if not MINI_GAME_COMMAND_RE.match(event.get_plaintext().strip()):
        return None
    feature_scopes = passive_settings()
    if (
        not is_feature_group(int(event.group_id), feature_scopes.groups("game"))
        or feature_scopes.is_game_globally_enabled()
    ):
        return None
    message = render_live_game_reminder(zhijiang_live_guard().active_entries())
    if message is None:
        return None
    # A normal member gets an explicit mention. Operators still need the same
    # explanation, but a plain reply avoids unnecessarily pinging them.
    if user_role(int(event.user_id)) is UserRole.USER:
        return MessageSegment.at(int(event.user_id)) + " " + message
    return MessageSegment.text(message)


def is_active_filtered_command(event: MessageEvent) -> bool:
    text = event.get_plaintext().lstrip()
    if not text.startswith(settings.command_prefix):
        return False
    user_id = int(event.user_id)
    if (
        is_super_admin(user_id)
        and FILTER_MANAGEMENT_COMMAND_RE.match(text)
    ):
        return False
    return database().active_filter_contains(user_id)


# Run before all matchers so unconfigured groups never reach downstream plugins.
# NoneBot accepts commands only with the fixed # prefix; ordinary group messages
# remain available to explicitly opt-in passive and AI listeners.
@event_preprocessor
async def _(event: MessageEvent):
    if is_active_filtered_command(event):
        raise IgnoredException(f"user {event.user_id} is in the active filter list")
    if isinstance(event, GroupMessageEvent) and not is_managed_group(
        int(event.group_id), settings.managed_group_ids
    ):
        raise IgnoredException(f"group {event.group_id} is outside the managed scope")


@event_postprocessor
async def _(bot: Bot, event: MessageEvent):
    """React to every other group mention after any command response is complete."""
    if not should_acknowledge_mention(event):
        return
    if database().passive_filter_contains(int(event.user_id)):
        return
    try:
        with passive_response_for(event):
            await call_qq_action(
                bot,
                "set_msg_emoji_like",
                message_id=int(event.message_id),
                emoji_id=random.choice(settings.mention_ack_emoji_ids),
                set=True,
            )
    except Exception:
        logger.debug("Mention reaction acknowledgement unavailable for message=%s", event.message_id)


# Consume mini-game commands when the feature is not enabled. Live-guard
# pauses explain the reason; other unavailable scopes remain silent.
disabled_game_command = on_message(rule=is_disabled_game_command, priority=-1, block=True)


@disabled_game_command.handle()
async def _(event: MessageEvent):
    message = live_guard_mini_game_reminder(event)
    if message is not None:
        await disabled_game_command.finish(message)
    await disabled_game_command.finish()
