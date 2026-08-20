from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.rule import Rule

from bot.config import settings
from bot.services.avatars import AvatarService
from bot.services.media import local_image_segment
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.qq_platform import call_qq_action
from bot.services.roles import is_super_admin
from bot.services.runtime import database, passive_settings
from bot.services.today_wife import TodayWifeService
from bot.services.today_wife_delivery import TodayWifeConclusionDelivery
from bot.services.today_wife_game import TodayWifeGameService
from bot.services.today_wife_result import first_draw_result_message


COMMANDS = frozenset(
    {
        "#今日老婆",
        "#今日缘分",
        "#离婚",
        "#解缘",
        "#我的缘分",
        "#我的老婆",
        "#群缘分",
        "#群老婆",
        "#强取",
        "#互动",
        "#清缘",
        "#确认清缘",
        "#取消清缘",
    }
)
service = TodayWifeService(database(), settings.timezone)
game_service = TodayWifeGameService(database(), settings.timezone)
feature_scopes = passive_settings()
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
group_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
clear_requests: dict[tuple[int, int], float] = {}
CLEAR_CONFIRMATION_SECONDS = 60
timezone = ZoneInfo(settings.timezone)
INTERACTION_INTENTS = frozenset({"靠近", "倾听", "回应", "修复", "助攻"})
MULTIPLE_INTERACTION_TARGET_MESSAGE = "一次互动只能 @ 一位群友。"
INVALID_INTERACTION_TARGET_MESSAGE = "互动目标必须是一位有效的群友，不能用 @全体 或无效 @ 代替。"
SELF_INTERACTION_TARGET_MESSAGE = "这次互动不能 @ 自己，也不会消耗次数。"


def _force_message(kind: str, record: dict[str, Any] | None = None) -> str:
    """Return concise, actionable feedback for the directed-draw command."""

    prefix = settings.command_prefix
    if kind == "missing":
        return f"用法：{prefix}强取 @其他用户"
    if kind == "multiple":
        return f"强取失败：一次只能指定一位群友。用法：{prefix}强取 @其他用户"
    if kind == "existing":
        if str((record or {}).get("status") or "") == "divorced":
            return "强取失败：你今天的重抽机会已经用过，不能再抽新的缘分。"
        target_name = str((record or {}).get("target_nickname") or "当前老婆")
        return f"强取失败：你今天已经有老婆（{target_name}）了；想换人请先 {prefix}离婚。"
    if kind == "invalid":
        return "强取失败：目标必须是当前群里除自己以外的一位群友。"
    return "强取失败：这次没有成功写入今日缘分，请稍后重试。"


def _today_wife_groups() -> tuple[int, ...]:
    return tuple(feature_scopes.groups("today_wife"))


conclusion_delivery = TodayWifeConclusionDelivery(game_service, renderer, _today_wife_groups)


def _command(event: MessageEvent) -> str:
    text = event.get_plaintext().strip()
    if text == "#强取" or text.startswith(("#强取 ", "#强取\t", "#强取@")):
        return "#强取"
    if text.startswith("#互动"):
        return "#互动"
    for command in ("#我的缘分", "#我的老婆", "#群缘分", "#群老婆"):
        if text == command or text.startswith(command + " "):
            return command
    return "#" + text[1:].lstrip() if text.startswith("#") else text


def _command_argument(event: GroupMessageEvent, command: str) -> str:
    text = event.get_plaintext().strip()
    return text[len(command):].strip() if text.startswith(command) else ""


def _is_today_wife_message(event: MessageEvent) -> bool:
    return isinstance(event, GroupMessageEvent) and _command(event) in COMMANDS


def _is_today_wife_activity(event: MessageEvent) -> bool:
    return (
        isinstance(event, GroupMessageEvent)
        and feature_scopes.is_feature_group_enabled("today_wife", int(event.group_id))
    )


def _clear_request_key(group_id: int, user_id: int) -> tuple[int, int]:
    return int(group_id), int(user_id)


def _clear_request_is_current(group_id: int, user_id: int) -> bool:
    deadline = clear_requests.get(_clear_request_key(group_id, user_id))
    return deadline is not None and deadline >= time.monotonic()


def _nickname(event: GroupMessageEvent) -> str:
    sender = event.sender
    return str(getattr(sender, "card", "") or getattr(sender, "nickname", "") or "这位群友")[:40]


def _unwrap_list(response: Any) -> list[dict[str, Any]]:
    value = response.get("data") if isinstance(response, dict) and "data" in response else response
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def _fetch_members(bot: Bot, group_id: int) -> list[dict[str, Any]] | None:
    try:
        response = await call_qq_action(bot, "get_group_member_list", group_id=int(group_id), no_cache=False)
    except Exception:
        logger.warning("Today-wife member list unavailable for group=%s", group_id, exc_info=True)
        return None
    members = _unwrap_list(response)
    return members or None


async def _avatars(rows: list[dict[str, Any]]) -> dict[int, Any]:
    try:
        return await avatar_service.prefetch(rows)
    except Exception:
        logger.warning("Today-wife avatar prefetch failed", exc_info=True)
        return {}


def _mentioned_users(event: GroupMessageEvent) -> tuple[int, ...]:
    targets: list[int] = []
    for segment in event.get_message():
        if segment.type != "at":
            continue
        value = str(segment.data.get("qq") or "")
        if value.isdigit() and int(value) > 0:
            targets.append(int(value))
    return tuple(targets)


def _mentioned_user(event: GroupMessageEvent) -> int | None:
    targets = _mentioned_users(event)
    return targets[0] if len(targets) == 1 else None


def _interaction_target(event: GroupMessageEvent, actor_id: int) -> tuple[int | None, str | None]:
    """Parse one interaction mention without silently turning it into no target."""

    segments = [segment for segment in event.get_message() if segment.type == "at"]
    if len(segments) > 1:
        return None, "multiple"
    if not segments:
        return None, None
    value = str(segments[0].data.get("qq") or "")
    if not value.isdigit() or int(value) <= 0:
        return None, "invalid"
    target_id = int(value)
    if target_id == int(actor_id):
        return None, "self"
    return target_id, None


def _force_target_id(event: GroupMessageEvent) -> int | None:
    """Return a single real member mention; reject @all and mixed mentions."""

    segments = [segment for segment in event.get_message() if segment.type == "at"]
    if len(segments) != 1:
        return None
    value = str(segments[0].data.get("qq") or "")
    return int(value) if value.isdigit() and int(value) > 0 else None


def _interaction_intent(event: GroupMessageEvent) -> str:
    """Read the optional first action word without treating an @ as prose."""

    argument = _command_argument(event, "#互动")
    if not argument:
        return "auto"
    intent = argument.split(maxsplit=1)[0]
    return intent if intent in INTERACTION_INTENTS else intent


@driver.on_bot_connect
async def _(bot: Bot) -> None:
    await conclusion_delivery.deliver_once(bot)


async def _deliver_today_wife_conclusions() -> None:
    target = next(iter(get_bots().values()), None)
    await conclusion_delivery.deliver_once(target)


@driver.on_startup
async def _start_today_wife_scheduler() -> None:
    scheduler.add_job(
        _deliver_today_wife_conclusions,
        "interval",
        seconds=30,
        id="today-wife-conclusion-delivery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Today-wife conclusion scheduler started; window=23:50-23:59")


@driver.on_shutdown
async def _stop_today_wife_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


# This listener deliberately stores only a deduplicated count.  It runs before
# command matchers so a command message is treated the same as any other group
# message, while never retaining its text.
today_wife_activity = on_message(rule=Rule(_is_today_wife_activity), priority=-100, block=False)


@today_wife_activity.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if int(event.user_id) == int(bot.self_id):
        return
    try:
        message_at = datetime.fromtimestamp(int(event.time), timezone)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        message_at = datetime.now(timezone)
    service.record_activity(
        f"{int(event.group_id)}:{int(event.message_id)}",
        int(event.group_id),
        int(event.user_id),
        message_at,
    )


today_wife = on_message(rule=Rule(_is_today_wife_message), priority=2, block=True)


@today_wife.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    command = _command(event)
    group_id = int(event.group_id)
    actor_id = int(event.user_id)
    clear_key = _clear_request_key(group_id, actor_id)
    if command not in {"#清缘", "#确认清缘", "#取消清缘"} and not feature_scopes.is_feature_group_enabled(
        "today_wife", group_id
    ):
        await today_wife.finish()
        return

    if command in {"#清缘", "#确认清缘", "#取消清缘"}:
        if not is_super_admin(actor_id):
            await today_wife.finish(service.state_message("clear_forbidden", group_id, actor_id))
        if command == "#清缘":
            clear_requests[clear_key] = time.monotonic() + CLEAR_CONFIRMATION_SECONDS
            await today_wife.finish(service.state_message("clear_warning", group_id, actor_id))
        if command == "#取消清缘":
            if clear_requests.pop(clear_key, None) is None:
                await today_wife.finish(
                    service.state_message("clear_cancel_missing", group_id, actor_id)
                )
            await today_wife.finish(service.state_message("clear_cancelled", group_id, actor_id))
        if not _clear_request_is_current(group_id, actor_id):
            clear_requests.pop(clear_key, None)
            await today_wife.finish(
                service.state_message("clear_confirm_missing", group_id, actor_id)
            )
        clear_requests.pop(clear_key, None)
        async with group_locks[group_id]:
            deleted = service.clear_group_records(group_id)
        await today_wife.finish(
            service.state_message("clear_success", group_id, actor_id, deleted=deleted)
        )

    async with group_locks[group_id]:
        locked = game_service.is_locked(group_id)
        if locked and command in {"#今日老婆", "#今日缘分", "#强取", "#离婚", "#解缘", "#互动"}:
            await today_wife.finish(service.state_message("locked", group_id, actor_id))
            return

        if command == "#强取":
            target_id = _force_target_id(event)
            at_count = sum(1 for segment in event.get_message() if segment.type == "at")
            if at_count == 0:
                await today_wife.finish(_force_message("missing"))
                return
            if at_count > 1:
                await today_wife.finish(_force_message("multiple"))
                return
            if target_id is None:
                await today_wife.finish(_force_message("invalid"))
                return
            members = await _fetch_members(bot, group_id)
            if members is None:
                await today_wife.finish(
                    service.state_message("member_list_unavailable", group_id, actor_id)
                )
                return
            outcome = service.force_draw(
                group_id,
                actor_id,
                _nickname(event),
                members,
                target_id=target_id,
            )
            if outcome.kind in {"force_existing", "existing", "existing_divorced"}:
                await today_wife.finish(_force_message("existing", outcome.record))
                return
            if outcome.kind == "force_invalid_target":
                await today_wife.finish(_force_message("invalid"))
                return
            if outcome.kind != "drawn" or outcome.record is None:
                if outcome.kind == "no_candidates":
                    await today_wife.finish(service.state_message("no_candidates", group_id, actor_id))
                else:
                    await today_wife.finish(_force_message("unknown"))
                return
            record = outcome.record
            avatars = await _avatars(
                [
                    {"user_id": int(record["actor_id"])},
                    {"user_id": int(record["target_id"])},
                ]
            )
            state = game_service.day_state(group_id)
            archive = game_service.personal_archive(group_id, actor_id)
            relation = next(
                (
                    item.get("relation")
                    for item in archive["own"]
                    if int(item.get("draw_index") or 0) == int(record.get("draw_index") or 0)
                ),
                {},
            )
            reveal = game_service.draw_reveal(record, relation or {})
            path = renderer.render_today_wife_game_draw(
                record,
                service.story_lines(record),
                service.context_lines(record),
                avatars,
                state,
                relation or {},
                draw_reveal=reveal,
                available_actions=reveal.get("action_options") or reveal.get("available_actions"),
            )
            if int(record.get("draw_index") or 0) == 1:
                await today_wife.finish(
                    first_draw_result_message(
                        event.message_id,
                        int(record["target_id"]),
                        path,
                        str(reveal.get("mention_lead") or "今天的缘分悄悄落在了"),
                    )
                )
                return
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return

        if command in {"#今日老婆", "#今日缘分"}:
            members = await _fetch_members(bot, group_id)
            if members is None:
                await today_wife.finish(
                    service.state_message("member_list_unavailable", group_id, actor_id)
                )
                return
            outcome = service.draw(group_id, actor_id, _nickname(event), members)
            if outcome.kind == "no_candidates":
                await today_wife.finish(service.state_message("no_candidates", group_id, actor_id))
                return
            record = outcome.record
            assert record is not None
            avatars = await _avatars(
                [
                    {"user_id": int(record["actor_id"])},
                    {"user_id": int(record["target_id"])},
                ]
            )
            if outcome.kind == "existing_divorced":
                archive = game_service.personal_archive(group_id, actor_id)
                relation = next(
                    (
                        item.get("relation")
                        for item in archive["own"]
                        if int(item.get("draw_index") or 0) == int(record.get("draw_index") or 0)
                    ),
                    {},
                )
                path = renderer.render_divorce(record, service.divorce_lines(record), avatars, relation or {})
                await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
                return
            state = game_service.day_state(group_id)
            archive = game_service.personal_archive(group_id, actor_id)
            relation = next(
                (item.get("relation") for item in archive["own"] if int(item.get("draw_index") or 0) == int(record.get("draw_index") or 0)),
                {},
            )
            reveal = game_service.draw_reveal(record, relation or {})
            path = renderer.render_today_wife_game_draw(
                record,
                service.story_lines(record),
                service.context_lines(record),
                avatars,
                state,
                relation or {},
                draw_reveal=reveal,
                available_actions=reveal.get("action_options") or reveal.get("available_actions"),
            )
            if outcome.kind == "drawn" and int(record.get("draw_index") or 0) == 1:
                await today_wife.finish(
                    first_draw_result_message(
                        event.message_id,
                        int(record["target_id"]),
                        path,
                        str(reveal.get("mention_lead") or "今天的缘分悄悄落在了"),
                    )
                )
                return
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return

        if command in {"#离婚", "#解缘"}:
            outcome = service.divorce(group_id, actor_id)
            if outcome.kind == "not_found":
                await today_wife.finish(service.state_message("not_found", group_id, actor_id))
                return
            if outcome.kind == "already_divorced":
                await today_wife.finish(service.state_message("already_divorced", group_id, actor_id))
                return
            record = outcome.record
            assert record is not None
            avatars = await _avatars([{"user_id": int(record["target_id"])}])
            archive = game_service.personal_archive(group_id, actor_id)
            relation = next(
                (
                    item.get("relation")
                    for item in archive["own"]
                    if int(item.get("draw_index") or 0) == int(record.get("draw_index") or 0)
                ),
                {},
            )
            path = renderer.render_divorce(record, service.divorce_lines(record), avatars, relation or {})
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return

        if command == "#互动":
            mentioned_id, target_error = _interaction_target(event, actor_id)
            if target_error == "multiple":
                await today_wife.finish(MULTIPLE_INTERACTION_TARGET_MESSAGE)
                return
            if target_error == "invalid":
                await today_wife.finish(INVALID_INTERACTION_TARGET_MESSAGE)
                return
            if target_error == "self":
                await today_wife.finish(SELF_INTERACTION_TARGET_MESSAGE)
                return
            result = game_service.interaction(
                group_id,
                actor_id,
                _nickname(event),
                mentioned_id,
                intent=_interaction_intent(event),
                source_message_id=event.message_id,
            )
            if result.kind == "prompt" and result.event is not None:
                prompt = {**result.event, "message": result.message}
                path = renderer.render_today_wife_action_prompt(prompt)
                await today_wife.finish(
                    MessageSegment.reply(event.message_id) + local_image_segment(path)
                )
                return
            if result.kind != "played" or result.event is None:
                await today_wife.finish(result.message)
                return
            avatar_ids = {actor_id}
            for item in result.event.get("effects", ()):
                if not isinstance(item, dict):
                    continue
                for key in ("actor_id", "target_id"):
                    try:
                        user_id = int(item.get(key) or 0)
                    except (TypeError, ValueError):
                        continue
                    if user_id > 0:
                        avatar_ids.add(user_id)
            rows = [{"user_id": user_id} for user_id in sorted(avatar_ids)]
            avatars = await _avatars(rows)
            path = renderer.render_today_wife_interaction(
                result.event,
                avatars,
                available_actions=result.event.get("action_options") or result.event.get("available_actions"),
            )
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return

        if command in {"#我的缘分", "#我的老婆"}:
            raw_page = _command_argument(event, command)
            if raw_page:
                try:
                    page = max(1, int(raw_page))
                except ValueError:
                    await today_wife.finish(service.player_message("history_page_invalid", group_id, actor_id))
                    return
                history = game_service.personal_history(group_id, actor_id, page)
                avatars = await _avatars([{"user_id": int(row["target_id"])} for row in history["rows"]])
                path = renderer.render_today_wife_history_archive(history, avatars)
                await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
                return
            archive = game_service.personal_archive(group_id, actor_id)
            avatar_rows = [
                {"user_id": int(row["target_id"])} for row in archive["own"]
            ] + [{"user_id": int(row["actor_id"])} for row in archive["incoming"]]
            avatars = await _avatars(avatar_rows)
            path = renderer.render_today_wife_archive(
                archive,
                avatars,
                available_actions=archive.get("action_options") or archive.get("available_actions"),
            )
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return

        raw_day = _command_argument(event, command)
        if raw_day == "历史":
            archive = game_service.group_archive(group_id)
            path = renderer.render_today_wife_group_archive(archive)
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return
        requested_day: date | None = None
        if raw_day:
            try:
                requested_day = date.fromisoformat(raw_day)
            except ValueError:
                await today_wife.finish(service.player_message("group_history_argument_invalid", group_id, actor_id))
                return
        story = game_service.group_story(group_id, requested_day=requested_day)
        if not story["full_detail"] and requested_day is not None:
            archive = game_service.group_archive(group_id)
            summary = next((item for item in archive["summaries"] if item["day"] == raw_day), None)
            if summary is None:
                await today_wife.finish(service.player_message("group_history_missing", group_id, actor_id))
                return
            path = renderer.render_today_wife_group_archive({"summaries": [summary], "detail_retention_days": 7})
            await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
            return
        rows = story["records"]
        avatars = await _avatars(
            [
                {"user_id": int(user_id)}
                for row in rows
                for user_id in (int(row["actor_id"]), int(row["target_id"]))
            ]
        )
        day = str(story["day"])
        path = renderer.render_group_today_wife(
            rows,
            avatars,
            day,
            {"title": story["day_state"]["script_title"]},
            str(story["spotlight"]),
        )
        await today_wife.finish(MessageSegment.reply(event.message_id) + local_image_segment(path))
