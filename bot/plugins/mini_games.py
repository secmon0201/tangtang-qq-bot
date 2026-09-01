from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.rule import Rule

from bot.config import settings
from bot.services.avatars import AvatarService
from bot.services.forward import build_forward_nodes
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.mini_games import BOMB, DICE, GUESS, ROULETTE, GameEvent, MiniGameService
from bot.services.qq_platform import call_qq_action
from bot.services.media import local_image_segment
from bot.services.roles import is_super_admin
from bot.services.runtime import database, group_domains, passive_settings


db = database()
feature_scopes = passive_settings()
domains = group_domains()
service = MiniGameService(db, cursed_guess_numbers=settings.guess_cursed_numbers)
renderer = MiniGameReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
    settings.command_prefix,
)
avatar_service = AvatarService(
    settings.avatar_cache_dir,
    settings.avatar_base_url,
    settings.avatar_timeout,
    settings.avatar_cache_ttl,
    refresh_interval=settings.avatar_refresh_interval,
    refresh_cooldown=settings.avatar_refresh_cooldown,
    max_refresh_per_call=settings.avatar_refresh_max_per_call,
    concurrency=settings.avatar_refresh_concurrency,
)
group_avatar_service = AvatarService(
    settings.avatar_cache_dir / "groups",
    "https://p.qlogo.cn/gh/{user_id}/{user_id}/100",
    settings.avatar_timeout,
    settings.avatar_cache_ttl,
    refresh_interval=settings.avatar_refresh_interval,
    refresh_cooldown=settings.avatar_refresh_cooldown,
    max_refresh_per_call=settings.avatar_refresh_max_per_call,
    concurrency=settings.avatar_refresh_concurrency,
)
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
group_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
clear_requests: dict[tuple[int, int], float] = {}
CLEAR_CONFIRMATION_SECONDS = 60
MENU_COMMANDS = {"#游戏列表", "#小游戏列表"}
RANKING_COMMANDS = {
    "#转盘榜": (ROULETTE, False),
    "#转盘总榜": (ROULETTE, True),
    "#俄罗斯转盘榜单": (ROULETTE, False),
    "#俄罗斯转盘总榜单": (ROULETTE, True),
    "#炸弹榜": (BOMB, False),
    "#炸弹总榜": (BOMB, True),
    "#定时炸弹榜单": (BOMB, False),
    "#定时炸弹总榜单": (BOMB, True),
    "#骰子榜": (DICE, False),
    "#骰子总榜": (DICE, True),
    "#幸运骰局榜单": (DICE, False),
    "#幸运骰局总榜单": (DICE, True),
    "#猜数榜": (GUESS, False),
    "#猜数总榜": (GUESS, True),
    "#猜数字榜单": (GUESS, False),
    "#猜数字总榜单": (GUESS, True),
}

MINI_GAME_COMMAND_RE = re.compile(
    r"^#\s*(?:丢给(?=\s|@|$)|猜(?:\s*.*)?|装弹\s*成语(?:\s*.*)?|(?:装填|开枪|装弹|骰子|(?:小)?游戏列表|转盘(?:总)?榜|炸弹(?:总)?榜|骰子(?:总)?榜|猜数(?:总)?榜|"
    r"俄罗斯转盘(?:总)?榜单|定时炸弹(?:总)?榜单|幸运骰局(?:总)?榜单|猜数字(?:总)?榜单|清游|确认|取消)(?:\s|$))"
)
IDIOM_BOMB_START_RE = re.compile(r"^#\s*装弹\s*成语\s*(?:(专业|娱乐)\s*)?(\d+)?\s*$")
IDIOM_BOMB_START_PREFIX_RE = re.compile(r"^#\s*装弹\s*成语")
IDIOM_BOMB_THROW_RE = re.compile(r"^([\u4e00-\u9fff]{4})\s*#\s*丢给(?=\s|@|$)")
IDIOM_BOMB_THROW_PREFIX_RE = re.compile(r"^[\u4e00-\u9fff]*\s*#\s*丢给(?=\s|@|$)")
GUESS_VALUE_RE = re.compile(r"^#猜\s*(.*)$")
FAILED_THROW_TEXTS = (
    "🤔💨 没有丢成功，请用 #丢给 @群成员。",
    "🧨🚫 炸弹没能送出去，要真的 @ 一位群成员才算数。",
    "📣🔎 没找到有效的 @ 对象，按 #丢给 @群成员 再试一次。",
)
FAILED_SELF_THROW_TEXTS = (
    "🙅🔄 不能丢给自己，快找一位群友接盘吧。",
    "😵💣 自己接自己不算，#丢给 @另一位群成员 才有效。",
)


def _command(event: MessageEvent) -> str:
    text = event.get_plaintext().strip()
    return "#" + text[1:].lstrip() if text.startswith("#") else text


def _is_mini_game_message(event: MessageEvent) -> bool:
    text = event.get_plaintext().strip()
    return isinstance(event, GroupMessageEvent) and bool(
        MINI_GAME_COMMAND_RE.match(_command(event))
        or IDIOM_BOMB_START_PREFIX_RE.match(_command(event))
        or IDIOM_BOMB_THROW_PREFIX_RE.match(text)
    )


def _guess_value(command: str) -> int | None:
    match = GUESS_VALUE_RE.fullmatch(command)
    if match is None:
        return None
    value = match.group(1).strip()
    return int(value) if re.fullmatch(r"[0-9]+", value) else None


def _nickname(event: MessageEvent) -> str:
    sender = getattr(event, "sender", None)
    return str(
        getattr(sender, "card", "")
        or getattr(sender, "nickname", "")
        or "这位群友"
    )[:40]


def _unwrap(response: Any) -> dict[str, Any]:
    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        return dict(response["data"])
    return dict(response) if isinstance(response, dict) else {}


def _unwrap_list(response: Any) -> list[dict[str, Any]]:
    value = response.get("data") if isinstance(response, dict) and "data" in response else response
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def _remember_group_name(bot: Bot, group_id: int) -> None:
    try:
        response = await call_qq_action(bot, "get_group_info", group_id=int(group_id), no_cache=False)
        name = str(_unwrap(response).get("group_name") or "").strip()
        if name:
            db.set_group_info(group_id, name[:80])
    except Exception:
        logger.debug("Mini-game group info is unavailable for group=%s", group_id)


async def _mentioned_member(bot: Bot, event: GroupMessageEvent) -> tuple[int, str] | None:
    targets: list[int] = []
    for segment in event.get_message():
        if segment.type != "at":
            continue
        value = str(segment.data.get("qq") or "")
        if value.isdigit() and int(value) > 0:
            targets.append(int(value))
    if len(targets) != 1 or targets[0] == int(bot.self_id):
        return None
    target_id = targets[0]
    try:
        response = await call_qq_action(
            bot,
            "get_group_member_info",
            group_id=int(event.group_id),
            user_id=target_id,
            no_cache=False,
        )
    except Exception:
        return None
    member = _unwrap(response)
    name = str(member.get("card") or member.get("nickname") or "这位群友")[:40]
    return target_id, name


async def _reply_failed_throw(
    matcher: Any, event: GroupMessageEvent, text: str | None = None
) -> None:
    text = text or secrets.choice(FAILED_THROW_TEXTS)
    await matcher.finish(MessageSegment.reply(event.message_id) + text)


def _mute_duration(event: GameEvent) -> int:
    if event.game_type == GUESS:
        return secrets.randbelow(31) + 30
    return secrets.randbelow(91) + 30


async def _apply_event_mutes(bot: Bot, event: GameEvent) -> GameEvent:
    if not event.mute_user_ids or not feature_scopes.is_game_mute_enabled(event.group_id):
        return event
    try:
        members = _unwrap_list(
            await call_qq_action(
                bot,
                "get_group_member_list",
                group_id=int(event.group_id),
                no_cache=False,
            )
        )
    except Exception:
        members = []
    member = next(
        (
            item
            for item in members
            if isinstance(item, dict) and int(item.get("user_id", 0) or 0) == int(bot.self_id)
        ),
        None,
    )
    if member is None:
        try:
            member = _unwrap(
                await call_qq_action(
                    bot,
                    "get_group_member_info",
                    group_id=int(event.group_id),
                    user_id=int(bot.self_id),
                    no_cache=True,
                )
            )
        except Exception:
            logger.warning("Mini-game mute skipped: unable to verify bot role for group=%s", event.group_id)
            return event
    if str(member.get("role", "member")) not in {"owner", "admin"}:
        logger.warning("Mini-game mute skipped: bot role=%s for group=%s", member.get("role"), event.group_id)
        return event
    duration = _mute_duration(event)
    half_mute_user_ids = {int(user_id) for user_id in event.half_mute_user_ids}
    muted_user_ids: list[int] = []
    for user_id in dict.fromkeys(int(value) for value in event.mute_user_ids if int(value) > 0):
        if user_id == int(bot.self_id):
            continue
        try:
            user_duration = max(1, duration // 2) if user_id in half_mute_user_ids else duration
            await call_qq_action(
                bot,
                "set_group_ban",
                group_id=int(event.group_id),
                user_id=user_id,
                duration=user_duration,
            )
            logger.info(
                "Mini-game mute applied (group=%s, user=%s, duration=%ss)",
                event.group_id,
                user_id,
                user_duration,
            )
            muted_user_ids.append(user_id)
        except Exception:
            logger.warning(
                "Mini-game mute failed (group=%s, user=%s, duration=%s)",
                event.group_id,
                user_id,
                duration,
                exc_info=True,
            )
    if not muted_user_ids:
        return event
    if half_mute_user_ids:
        return replace(
            event,
            text=f"{event.text}\n🔇 触发者禁言 {duration} 秒；其他参与者禁言 {max(1, duration // 2)} 秒。",
        )
    return replace(event, text=f"{event.text}\n🔇 禁言 {duration} 秒。")


def _event_message(event: GameEvent) -> Any:
    remaining = event.text
    parts: list[Any] = []
    replaced_user_ids: set[int] = set()
    replacements = [
        (str(name), int(user_id))
        for name, user_id in event.mention_replacements
        if str(name).strip() and int(user_id) > 0
    ]
    while remaining and replacements:
        matches = [
            (remaining.find(name), index, name, user_id)
            for index, (name, user_id) in enumerate(replacements)
            if remaining.find(name) >= 0
        ]
        if not matches:
            break
        offset, replacement_index, name, user_id = min(matches, key=lambda item: (item[0], -len(item[2])))
        if offset:
            parts.append(MessageSegment.text(remaining[:offset]))
        parts.append(MessageSegment.at(user_id))
        replaced_user_ids.add(user_id)
        remaining = remaining[offset + len(name) :]
        replacements.pop(replacement_index)
    if remaining:
        parts.append(MessageSegment.text(remaining))
    if not parts:
        parts.append(MessageSegment.text(event.text))
    fallback_mentions = [
        MessageSegment.at(int(user_id)) + " "
        for user_id in dict.fromkeys(event.mention_user_ids)
        if int(user_id) > 0 and int(user_id) not in replaced_user_ids
    ]
    if fallback_mentions:
        parts = [*fallback_mentions, *parts]
    return sum(parts[1:], parts[0])
async def _send_event(matcher: Any, event: GameEvent, bot: Bot | None = None) -> None:
    if bot is not None:
        event = await _apply_event_mutes(bot, event)
    await matcher.finish(_event_message(event))


def _ranking_fallback(payload: dict[str, Any]) -> str:
    lines = [str(payload["title"]), "参局次数仅作注释"]
    group_codes = {
        int(row["group_id"]): f"G{index}" for index, row in enumerate(payload["groups"], 1)
    }
    for section in payload["sections"]:
        lines.append(f"\n{section['title']}")
        rows = section["rows"]
        if not rows:
            lines.append("暂无战绩")
            continue
        for row in rows:
            codes = "、".join(group_codes[group_id] for group_id in row.get("group_ids", ()) if group_id in group_codes)
            lines.append(
                f"{row['rank']}. {row['nickname']}  {section['value_label']} {row['value']} 次  参局 {row['games']} 次"
                + (f"  群 {codes}" if codes else "")
            )
    if payload["global"] and payload["groups"]:
        lines.append("\n涉及群聊：")
        lines.extend(f"G{index} {row['group_name']}" for index, row in enumerate(payload["groups"], 1))
    return "\n".join(lines)


async def _send_menu(bot: Bot, matcher: Any, group_id: int) -> None:
    paths = [renderer.render_menu(), *renderer.render_game_details()]
    labels = [
        "小游戏清单",
        "俄罗斯转盘玩法与随机事件",
        "定时炸弹玩法与随机事件",
        "幸运骰局玩法与随机事件",
        "猜数字玩法与随机事件",
        "定时炸弹 · 成语接龙 DLC",
    ]
    try:
        await call_qq_action(
            bot,
            "send_group_forward_msg",
            group_id=int(group_id),
            messages=build_forward_nodes(labels, paths, bot.self_id, title="糖糖小游戏"),
        )
    except Exception:
        logger.exception("Mini-game menu forward failed")
        for path in paths:
            await matcher.send(local_image_segment(path))
    await matcher.finish()


async def _send_ranking(
    matcher: Any, game_type: str, group_id: int, *, total: bool
) -> None:
    domain = domains.domain_for_group(group_id)
    visible_group_ids = (
        domains.domain_groups(domain.domain_id) if total and domain is not None else None
    )
    payload = service.ranking(
        game_type,
        None if total else group_id,
        visible_group_ids=visible_group_ids,
        include_group_details=bool(total and domain is not None and domain.mode == "cluster"),
    )
    rows = [
        {"user_id": int(row["user_id"])}
        for section in payload["sections"]
        for row in section["rows"]
    ]
    avatars = await avatar_service.prefetch(rows)
    group_avatars = await group_avatar_service.prefetch(
        [{"user_id": int(row["group_id"])} for row in payload["groups"]]
    )
    path = renderer.render_ranking(payload, avatars, group_avatars)
    await matcher.finish(local_image_segment(path))


async def _send_due_event(bot: Bot, event: GameEvent) -> None:
    if not event.announce:
        return
    event = await _apply_event_mutes(bot, event)
    try:
        await call_qq_action(bot, "send_group_msg", group_id=event.group_id, message=_event_message(event))
    except Exception:
        logger.exception("Failed to send mini-game timeout for group=%s", event.group_id)


async def _send_pending_random_events(matcher: Any, group_id: int) -> None:
    for event in service.trigger_due_random_events(group_id=group_id):
        if event.announce:
            await matcher.send(_event_message(event))


def _clear_request_key(group_id: int, user_id: int) -> tuple[int, int]:
    return int(group_id), int(user_id)


def _clear_request_is_current(group_id: int, user_id: int) -> bool:
    deadline = clear_requests.get(_clear_request_key(group_id, user_id))
    return deadline is not None and deadline >= time.monotonic()


async def _maintain_games() -> None:
    events = service.trigger_due_random_events()
    events.extend(service.expire_due())
    if not events:
        return
    bots = tuple(get_bots().values())
    if not bots:
        return
    bot = bots[0]
    for event in events:
        await _send_due_event(bot, event)


@driver.on_startup
async def _start_mini_game_scheduler() -> None:
    scheduler.add_job(_maintain_games, "interval", seconds=2, id="mini-game-maintenance", replace_existing=True)
    scheduler.start()
    logger.info("Mini-game scheduler started; game duration=%ss", 120)


@driver.on_shutdown
async def _stop_mini_game_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


mini_games = on_message(rule=Rule(_is_mini_game_message), priority=2, block=True)


@mini_games.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    command = _command(event)
    group_id = int(event.group_id)
    actor_id = int(event.user_id)
    clear_key = _clear_request_key(group_id, actor_id)
    logger.info(
        "Mini-game command dispatched (group={}, user={}, command={!r})",
        group_id,
        actor_id,
        command,
    )

    if command in {"#清游", "#确认", "#取消"}:
        if not is_super_admin(actor_id):
            await mini_games.finish("🔒 只有超级管理员可以管理本群小游戏战绩。")
        if command == "#清游":
            clear_requests[clear_key] = time.monotonic() + CLEAR_CONFIRMATION_SECONDS
            await mini_games.finish(
                "⚠️🗑️ 即将永久清空本群小游戏的全部战绩与对局记录！\n"
                "60 秒内发送 #确认 执行，发送 #取消 放弃。"
            )
        if command == "#取消":
            if clear_requests.pop(clear_key, None) is None:
                await mini_games.finish("🫧 当前没有待取消的清档操作。")
            await mini_games.finish("🛡️ 已取消，本群小游戏战绩保持不变。")
        if not _clear_request_is_current(group_id, actor_id):
            clear_requests.pop(clear_key, None)
            await mini_games.finish("⌛ 没有可确认的清档请求，先发送 #清游。")
        clear_requests.pop(clear_key, None)
        async with group_locks[group_id]:
            deleted = service.clear_group_records(group_id)
        await mini_games.finish(
            "🧹✨ 本群小游戏记录已清空！"
            f"\n已删除 {deleted['sessions']} 局对局、{deleted['stats']} 条战绩。"
        )

    if command in MENU_COMMANDS:
        await _send_menu(bot, mini_games, group_id)
        return

    if (
        not feature_scopes.is_game_globally_enabled()
        or not domains.feature_enabled(group_id, "mini_games")
    ):
        await mini_games.finish()
        return

    if command in RANKING_COMMANDS:
        game_type, is_total = RANKING_COMMANDS[command]
        await _send_ranking(
            mini_games,
            game_type,
            group_id,
            total=is_total,
        )

    logger.info("Mini-game command waiting for group lock (group={}, command={!r})", group_id, command)
    async with group_locks[group_id]:
        logger.info("Mini-game command acquired group lock (group={}, command={!r})", group_id, command)
        await _send_pending_random_events(mini_games, group_id)
        idiom_start = IDIOM_BOMB_START_RE.fullmatch(command)
        if idiom_start is not None:
            ruleset = "professional" if idiom_start.group(1) == "专业" else "entertainment"
            duration = int(idiom_start.group(2) or 120)
            outcome = service.start_bomb(
                group_id,
                actor_id,
                _nickname(event),
                idiom_mode=True,
                idiom_ruleset=ruleset,
                duration_seconds=duration,
            )
            if outcome.kind == "bomb_idiom_started":
                asyncio.create_task(_remember_group_name(bot, group_id))
            await _send_event(mini_games, outcome, bot)
        if IDIOM_BOMB_START_PREFIX_RE.match(command):
            await mini_games.finish(
                "⏱️📚 成语炸弹请用 #装弹成语[专业] [60-600]，默认娱乐模式；例如 #装弹成语专业 180。"
            )
        if command == "#装填":
            outcome = service.start_roulette(group_id, actor_id, _nickname(event))
            if outcome.kind == "roulette_started":
                asyncio.create_task(_remember_group_name(bot, group_id))
            await _send_event(mini_games, outcome, bot)
        if command == "#开枪":
            await _send_event(mini_games, service.fire(group_id, actor_id, _nickname(event)), bot)
        if command == "#装弹":
            outcome = service.start_bomb(group_id, actor_id, _nickname(event))
            if outcome.kind == "bomb_started":
                asyncio.create_task(_remember_group_name(bot, group_id))
            await _send_event(mini_games, outcome, bot)
        raw_text = event.get_plaintext().strip()
        idiom_throw = IDIOM_BOMB_THROW_RE.match(raw_text)
        is_plain_throw = command.startswith("#丢给")
        is_idiom_throw = IDIOM_BOMB_THROW_PREFIX_RE.match(raw_text) is not None
        if is_plain_throw or is_idiom_throw:
            idiom_mode = service.bomb_is_idiom_mode(group_id)
            if idiom_mode and idiom_throw is None:
                await mini_games.finish(
                    MessageSegment.reply(event.message_id)
                    + "🀄📝 本局请按「四字词 #丢给 @群成员」传递，例如：画蛇添足 #丢给 @群友。"
                )
            target = await _mentioned_member(bot, event)
            if target is None:
                await _reply_failed_throw(mini_games, event)
                return
            target_id, target_name = target
            if target_id == actor_id:
                await _reply_failed_throw(mini_games, event, secrets.choice(FAILED_SELF_THROW_TEXTS))
                return
            outcome = service.throw_bomb(
                group_id,
                actor_id,
                _nickname(event),
                target_id,
                target_name,
                idiom=idiom_throw.group(1) if idiom_throw is not None else None,
            )
            await _send_event(mini_games, outcome, bot)
        if command == "#骰子":
            outcome = service.roll_dice(group_id, actor_id, _nickname(event))
            if outcome.kind == "dice_roll":
                asyncio.create_task(_remember_group_name(bot, group_id))
            await _send_event(mini_games, outcome, bot)
        if command == "#猜数":
            outcome = service.start_guess(group_id, actor_id, _nickname(event))
            if outcome.kind == "guess_started":
                asyncio.create_task(_remember_group_name(bot, group_id))
            await _send_event(mini_games, outcome, bot)
        if command.startswith("#猜") and command != "#猜数":
            outcome = service.guess_number(
                group_id, actor_id, _nickname(event), _guess_value(command)
            )
            if outcome.kind == "guess_cursed":
                try:
                    await call_qq_action(bot, "delete_msg", message_id=int(event.message_id))
                except Exception:
                    logger.warning(
                        "Cursed guess recall failed (group=%s, message=%s)",
                        group_id,
                        event.message_id,
                        exc_info=True,
                    )
            await _send_event(mini_games, outcome, bot)
