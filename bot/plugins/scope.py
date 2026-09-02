from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Iterable
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from nonebot import get_driver, logger, on_message
from nonebot.exception import IgnoredException
from nonebot.message import event_postprocessor, event_preprocessor
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment

from bot.config import settings
from bot.services.pacing import passive_response_for
from bot.services.group_domains import FEATURES
from bot.services.qq_platform import QQPlatform, QQPlatformError, call_qq_action
from bot.services.replies import install_matcher_quote_replies
from bot.services.roles import UserRole, is_super_admin, user_role
from bot.services.runtime import database, group_domains, passive_settings, zhijiang_live_guard
from bot.services.zhijiang_live_reminders import render_live_game_reminder


install_matcher_quote_replies()

FILTER_MANAGEMENT_COMMAND_RE = re.compile(
    r"^#\s*(?:主动过滤|被动过滤|群过滤|移除主动过滤|移除被动过滤|移除群过滤|主动过滤列表|被动过滤列表|群过滤列表)(?:\s|$)"
)
driver = get_driver()
zone = ZoneInfo(settings.timezone)
disabled_notices: dict[tuple[int, str], float] = {}

def is_managed_group(group_id: int, managed_group_ids: Iterable[int]) -> bool:
    return int(group_id) in {int(item) for item in managed_group_ids}


MINI_GAME_NON_PLAY_COMMANDS = frozenset(
    {
        "#游戏列表",
        "#小游戏列表",
    }
)
MINI_GAME_COMMAND_RE = re.compile(
    r"^(?:[\u4e00-\u9fff]*\s*)?#\s*(?:丢给(?=\s|@|$)|猜(?:\s*.*)?|装弹\s*成语(?:\s*.*)?|(?:装填|开枪|装弹|骰子|(?:小)?游戏列表|转盘(?:总)?榜|炸弹(?:总)?榜|骰子(?:总)?榜|猜数(?:总)?榜|"
    r"俄罗斯转盘(?:总)?榜单|定时炸弹(?:总)?榜单|幸运骰局(?:总)?榜单|猜数字(?:总)?榜单)(?:\s|$))"
)
MINI_GAME_START_COMMAND_RE = re.compile(
    r"^#\s*(?:装填|装弹(?:\s*成语(?:\s*.*)?)?|骰子|猜数)(?:\s|$)"
)
FEATURE_COMMAND_PATTERNS = (
    (
        "speech_ranking",
        re.compile(
            r"^#\s*(?:发言排行|发言榜|统计|集群发言排行|集群发言榜|集群统计|[Aa]海岸发言排行|[Aa]海岸发言榜|[Aa]海岸统计)(?:\s|$)"
        ),
    ),
    ("nte", re.compile(r"^#?\s*nte(?:\s|$|[\u4e00-\u9fff])", re.IGNORECASE)),
    ("zhijiang_calendar", re.compile(r"^#\s*(?:枝江直播|直播日程|本周直播|今日直播|明日直播)(?:\s|$)")),
    ("mini_games", MINI_GAME_COMMAND_RE),
    ("today_wife", re.compile(r"^#\s*(?:今日老婆|今日缘分|强取|我的缘分|群缘分|离婚|清缘)(?:\s|$)")),
    ("speech_archive", re.compile(r"^#\s*(?:发言记录|发言搜索|发言画像|画像)(?:\s|$)")),
    ("hourly", re.compile(r"^#\s*(?:准时报点|整点报时)(?:\s|$)")),
)


def is_feature_group(group_id: int, enabled_group_ids: Iterable[int]) -> bool:
    return int(group_id) in {int(item) for item in enabled_group_ids}


def is_bot_mentioned(event: MessageEvent) -> bool:
    return bool(event.is_tome())


def should_acknowledge_mention(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent) and is_bot_mentioned(event)


def mention_chat_is_available(group_id: int) -> bool:
    return (
        passive_settings().is_chat_globally_enabled("mention_chat")
        and group_domains().feature_enabled(int(group_id), "mention_chat")
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
    enabled = group_domains().feature_enabled(group_id, "mini_games")
    global_enabled = passive_settings().is_game_globally_enabled()
    live_guarded = (
        group_domains().effective_feature_enabled(group_id, "live_guard")
        and bool(zhijiang_live_guard().active_entries())
        and bool(MINI_GAME_START_COMMAND_RE.match(text))
    )
    suppressed = database().is_managed_group(group_id) and (
        not enabled or not global_enabled or live_guarded
    )
    logger.info(
        "Mini-game command gate (group={}, user={}, command={!r}, enabled={}, global_enabled={}, live_guarded={}, suppressed={})",
        group_id,
        event.user_id,
        text,
        enabled,
        global_enabled,
        live_guarded,
        suppressed,
    )
    return suppressed


def live_guard_mini_game_reminder(event: MessageEvent) -> MessageSegment | None:
    """Build a notice only when the live guard, rather than a manual switch, paused games."""
    if not isinstance(event, GroupMessageEvent):
        return None
    if not MINI_GAME_COMMAND_RE.match(event.get_plaintext().strip()):
        return None
    group_id = int(event.group_id)
    if not (
        group_domains().feature_enabled(group_id, "mini_games")
        and passive_settings().is_game_globally_enabled()
        and group_domains().effective_feature_enabled(group_id, "live_guard")
        and MINI_GAME_START_COMMAND_RE.match(event.get_plaintext().strip())
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


def disabled_feature_for(text: str, group_id: int) -> str | None:
    normalized = str(text).strip()
    for feature_key, pattern in FEATURE_COMMAND_PATTERNS:
        if pattern.match(normalized) and not group_domains().effective_feature_enabled(
            int(group_id), feature_key
        ):
            return feature_key
    return None


async def _send_disabled_notice(bot: Bot, event: GroupMessageEvent, feature_key: str) -> None:
    key = (int(event.group_id), str(feature_key))
    now = monotonic()
    if now - disabled_notices.get(key, 0.0) < 60:
        return
    disabled_notices[key] = now
    label = FEATURES[feature_key].label
    try:
        await bot.send(event, f"本群已关闭{label}。")
    except Exception:
        logger.debug("Unable to send disabled feature notice: group=%s feature=%s", *key)


@driver.on_bot_connect
async def _sync_joined_groups(bot: Bot) -> None:
    platform = QQPlatform(bot)
    try:
        groups = await platform.group_list()
    except QQPlatformError:
        logger.exception("Unable to synchronize the live QQ group list")
        return
    live_ids: set[int] = set()
    for group in groups:
        group_id = int(group["group_id"])
        live_ids.add(group_id)
        joined_at: str | None = None
        try:
            member = await platform.member_info(group_id, int(bot.self_id))
            join_time = int(member.get("join_time") or 0)
            if join_time > 0:
                joined_at = datetime.fromtimestamp(join_time, zone).isoformat(timespec="seconds")
        except (QQPlatformError, TypeError, ValueError, OSError):
            logger.warning("Unable to read bot join_time for group=%s", group_id)
        group_domains().ensure_group(
            group_id,
            group_name=str(group.get("group_name") or ""),
            joined_at=joined_at,
        )
        if joined_at is not None:
            database().set_group_joined_at(group_id, joined_at, overwrite=True)

    for row in database().managed_groups():
        group_id = int(row["group_id"])
        if group_id not in live_ids:
            group_domains().disable_group(group_id)


# Run before all matchers. A newly observed QQ group is registered as an
# independent domain; old rows are reactivated without resetting their intent.
@event_preprocessor
async def _(bot: Bot, event: MessageEvent):
    if isinstance(event, GroupMessageEvent):
        group_id = int(event.group_id)
        if not database().is_managed_group(group_id):
            timestamp = int(getattr(event, "time", 0) or 0)
            joined_at = (
                datetime.fromtimestamp(timestamp, zone).isoformat(timespec="seconds")
                if timestamp > 0
                else datetime.now(zone).isoformat(timespec="seconds")
            )
            group_domains().ensure_group(group_id, joined_at=joined_at)
        text = event.get_plaintext().lstrip()
        management_command = bool(FILTER_MANAGEMENT_COMMAND_RE.match(text))
        if database().group_filter_contains(group_id, int(event.user_id)) and not management_command:
            raise IgnoredException(
                f"user {event.user_id} is in the group filter list for {group_id}"
            )
        disabled_feature = disabled_feature_for(text, group_id)
        if disabled_feature is not None:
            await _send_disabled_notice(bot, event, disabled_feature)
            raise IgnoredException(
                f"feature {disabled_feature} is disabled for group {group_id}"
            )
    if is_active_filtered_command(event):
        raise IgnoredException(f"user {event.user_id} is in the active filter list")


@event_postprocessor
async def _(bot: Bot, event: MessageEvent):
    """React to every other group mention after any command response is complete."""
    if not should_acknowledge_mention(event):
        return
    if not mention_chat_is_available(int(event.group_id)):
        return
    if (
        database().passive_filter_contains(int(event.user_id))
        or database().group_filter_contains(int(event.group_id), int(event.user_id))
    ):
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
