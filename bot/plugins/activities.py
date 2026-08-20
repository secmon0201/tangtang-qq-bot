from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from bot.application.admin_ui import (
    cached_group_avatar_paths as cached_activity_group_avatar_paths,
    group_avatar_paths as activity_group_avatar_paths,
)
from bot.application.local_features import FeatureRequest, register_local_feature
from bot.config import settings
from bot.services.activities import (
    ACTIVE_STATUSES,
    ACTIVITY_LOTTERY,
    ActivityService,
    format_local_time,
    masked_user_id,
    parse_activity_id,
    parse_create_payload,
    parse_group_ids,
    parse_update_payload,
)
from bot.services.avatars import AvatarService
from bot.services.forward import build_forward_nodes
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action
from bot.services.reports import ReportRenderer
from bot.services.runtime import database, passive_settings


db = database()
feature_scopes = passive_settings()
service = ActivityService(db, lambda: feature_scopes.groups("activity"))
renderer = ReportRenderer(
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
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


def group_id(event: MessageEvent) -> int | None:
    return int(event.group_id) if isinstance(event, GroupMessageEvent) else None


def sender_value(event: MessageEvent, name: str) -> str:
    sender = getattr(event, "sender", None)
    return str(getattr(sender, name, "") or "") if sender else ""


async def require_group(matcher: Any, event: MessageEvent) -> int:
    current = group_id(event)
    if current is None:
        await matcher.finish("活动功能请在活动开放群内使用。")
    if not service.allowed_group(current):
        await matcher.finish("当前群未开放跨群活动功能。")
    return current


async def require_activity_catalog_context(matcher: Any, event: MessageEvent) -> int | None:
    """Allow the bot owner to inspect public activity catalogues in private."""
    current = group_id(event)
    if current is not None:
        return await require_group(matcher, event)
    if int(event.user_id) not in settings.operator_ids:
        await matcher.finish("只有机器人所有者可以通过私聊查看活动清单。")
    return None


async def require_activity_access(matcher: Any, event: MessageEvent, activity: Any) -> int:
    current = await require_group(matcher, event)
    groups = {int(row["group_id"]) for row in db.activity_groups(int(activity["activity_id"]))}
    if current not in groups:
        await matcher.finish("当前群不是该活动的参与群。")
    return current


async def require_create_context(matcher: Any, event: MessageEvent) -> int | None:
    current = group_id(event)
    if current is None:
        if not service.can_create(event):
            await matcher.finish("只有超级管理员或配置的活动管理员可以通过私聊创建活动。群管理员请在活动有效群内使用。")
        return None
    if service.allowed_group(current):
        if not service.can_create(event):
            await matcher.finish("只有超级管理员、配置的活动管理员或本群群管理可以创建活动。")
        return current
    if int(event.user_id) in settings.operator_ids:
        return current
    await matcher.finish("当前群未开放跨群活动功能。")


async def require_management_context(matcher: Any, event: MessageEvent, activity: Any) -> int | None:
    if not service.can_manage(event, activity):
        await matcher.finish("只有超级管理员或活动管理员本人可以管理该活动。")
    return group_id(event)


def activity_view(row: Any) -> dict[str, Any]:
    result = dict(row)
    creator_id = int(row["creator_id"])
    creator_profile = db.user_profiles((creator_id,)).get(creator_id, {})
    result["creator_id"] = creator_id
    result["creator_nickname"] = str(creator_profile.get("nickname") or "活动发起人")
    result["status_label"] = service.state_label(str(row["status"]))
    result["visibility_label"] = (
        "公开参与" if str(row["visibility"] or "masked") == "public" else "脱敏参与"
    )
    result["starts_text"] = format_local_time(str(row["starts_at"]))
    result["ends_text"] = format_local_time(str(row["ends_at"]))
    return result


async def send_local_image(matcher: Any, path: Path, fallback: str) -> None:
    try:
        await matcher.send(local_image_segment(path))
    except Exception:
        logger.exception("Activity local image send failed")
        await matcher.finish(fallback)
    await matcher.finish()


async def require_activity_view_context(
    matcher: Any, event: MessageEvent, activity: Any
) -> tuple[int | None, bool]:
    full = service.can_manage(event, activity)
    current = group_id(event)
    if current is None:
        if not full:
            await matcher.finish("只有活动创建者或机器人所有者可以通过私聊查看该活动。")
        return None, True
    # A group chat is always public context. Even creators and the bot owner
    # receive the same masked cross-group view as ordinary members.
    await require_activity_access(matcher, event, activity)
    return current, False


async def send_activity_detail_result(
    bot: Bot,
    matcher: Any,
    event: MessageEvent,
    activity: Any,
    full: bool,
) -> None:
    activity_id = int(activity["activity_id"])
    group_rows = activity_group_rows(activity_id)
    group_avatar_paths = await activity_group_avatar_paths(group_rows)
    detail_path = renderer.render_activity_detail(
        activity_view(activity),
        group_rows,
        activity_prize_rows(activity_id),
        action_hint=activity_action_hint(activity_id, str(activity["status"])),
        group_avatar_paths=group_avatar_paths,
    )
    if activity["status"] != "ended" or activity["activity_type"] != ACTIVITY_LOTTERY:
        await send_local_image(matcher, detail_path, f"活动 #{activity_id} 详情生成失败。")
    winner_rows = winner_view(activity, db.activity_winners(activity_id), int(event.user_id), full)
    avatar_paths = await activity_avatar_paths(winner_rows)
    paths = [detail_path, *winner_pages(activity, winner_rows, avatar_paths)]
    messages = [f"活动 #{activity_id} 最终详情"] + [
        f"活动 #{activity_id} 获奖名单，第 {index}/{len(paths) - 1} 页"
        for index in range(1, len(paths))
    ]
    current = group_id(event)
    try:
        if current is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动开奖结果"),
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=current,
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动开奖结果"),
            )
    except Exception:
        logger.exception("Activity detail forward failed")
        for path in paths:
            await matcher.send(local_image_segment(path))
    await matcher.finish()


async def send_winner_result(
    bot: Bot,
    matcher: Any,
    event: MessageEvent,
    activity: Any,
    full: bool,
) -> None:
    activity_id = int(activity["activity_id"])
    winner_rows = winner_view(activity, db.activity_winners(activity_id), int(event.user_id), full)
    avatar_paths = await activity_avatar_paths(winner_rows)
    paths = winner_pages(activity, winner_rows, avatar_paths)
    messages = [
        f"活动 #{activity_id} 获奖名单，第 {index}/{len(paths)} 页"
        for index in range(1, len(paths) + 1)
    ]
    current = group_id(event)
    if len(paths) == 1:
        await send_local_image(matcher, paths[0], f"活动 #{activity_id} 获奖名单生成失败。")
    try:
        if current is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动获奖名单"),
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=current,
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动获奖名单"),
            )
    except Exception:
        logger.exception("Activity winner forward failed")
        for path in paths:
            await matcher.send(local_image_segment(path))
    await matcher.finish()


def prizes_text(activity_id: int) -> str:
    prizes = db.activity_prizes(activity_id)
    return "；".join(f"{row['prize_name']} × {row['quantity']}" for row in prizes)


def groups_text(activity_id: int) -> str:
    return "、".join(
        f"{row['group_name'] or row['group_id']}({row['group_id']})"
        for row in activity_group_rows(activity_id)
    )


def activity_group_rows(activity_id: int) -> list[dict[str, Any]]:
    """Structured group data for poster rendering; avoids parsing display strings."""
    rows = {int(row["group_id"]): dict(row) for row in db.activity_groups(activity_id)}
    return [rows[group_id] for group_id in settings.managed_order(rows)]


def activity_prize_rows(activity_id: int) -> list[dict[str, Any]]:
    return [dict(row) for row in db.activity_prizes(activity_id)]


def activity_help() -> str:
    prefix = settings.command_prefix
    return (
        "普通用户活动帮助（可复制版）\n\n"
        f"查看：\n{prefix}活动大厅\n{prefix}活动详情 ID\n{prefix}查看名单 ID\n{prefix}获奖名单 ID\n\n"
        f"参与：\n{prefix}报名 ID\n{prefix}取消报名 ID\n{prefix}我的活动\n\n"
        "所有角色统一使用 # 前缀，例如 #报名517、#报名 517、#报名：517 或 #报名#517。\n"
        f"ID 是创建成功后显示的数字。例如 ID：500 时，可发送 {prefix}报名500 或 {prefix}活动详情：500。\n"
        "普通用户不能创建、修改、取消或提前结束活动。"
    )


def activity_admin_help_sections() -> list[tuple[str, str, str]]:
    prefix = settings.command_prefix
    return [
        (
            "角色边界",
            "活动管理员：ACTIVITY_ADMIN_IDS 名单内 QQ\n或活动有效群内的群主、群管理员",
            "群主和群管理员在所管理活动群内自动获得权限；黑名单成员除外。活动管理员只能管理自己创建的活动。",
        ),
        (
            "创建活动",
            f"{prefix}创建活动\n活动名：名称\n开始时间：YYYY-MM-DD HH:MM\n结束时间：YYYY-MM-DD HH:MM\n类型：通报/抽奖\n参与群：群号1,群号2",
            "每项单独换行；说明、奖项和隐私可留空。抽奖必须填写奖项，参与群留空时使用全部活动开放群。",
        ),
        (
            "修改活动",
            f"{prefix}修改活动\n活动ID：517\n活动名：新名称\n结束时间：YYYY-MM-DD HH:MM\n隐私：公开",
            "每项单独换行；未填写或留空的字段保持原值。仅超级管理员可修改进行中的活动，更新后会通知所有参与群。",
        ),
        (
            "结束与取消",
            f"{prefix}提前结束 ID\n{prefix}取消活动 ID [原因]",
            "取消是软删除，保留报名和审计记录；活动管理员只能操作自己创建的活动。",
        ),
        (
            "用户操作",
            f"{prefix}活动大厅\n{prefix}活动详情 ID\n{prefix}报名 ID\n{prefix}取消报名 ID\n{prefix}查看名单 ID",
            "活动开放群内所有用户统一使用 # 前缀，包括活动管理员和超级管理员。",
        ),
    ]


def activity_admin_help_text() -> str:
    sections = activity_admin_help_sections()
    return "活动管理员帮助（可复制版）\n\n" + "\n\n".join(
        f"{title}\n{commands}\n说明：{note}" for title, commands, note in sections
    )


def activity_entry_tutorial_sections(operation: str) -> list[tuple[str, str, str]]:
    prefix = settings.command_prefix
    if operation == "create":
        return [
            (
                "全部字段（共 8 项）",
                "活动名\n开始时间\n结束时间\n类型\n说明\n奖项\n参与群\n隐私",
                "创建活动时使用“字段名：内容”逐行填写。中文冒号“：”和英文冒号“:”都可以使用。",
            ),
            (
                "必填字段",
                "活动名：活动名称\n开始时间：YYYY-M-D HH:MM\n结束时间：YYYY-M-D HH:MM\n类型：通报 或 抽奖",
                "活动名不能为空且最多 80 字；结束时间必须晚于开始时间。时间按机器人时区填写。",
            ),
            (
                "选填字段",
                "说明：活动说明\n奖项：奖项内容=数量\n参与群：群号1,群号2\n隐私：公开 或 脱敏",
                "说明最多 500 字；“=”前面是奖项内容，后面是数量，多个奖项用“；”分隔。抽奖活动必须填写奖项，通报活动不填写奖项；参与群留空时使用全部活动开放群；隐私留空默认为脱敏。",
            ),
            (
                "完整创建案例",
                f"{prefix}创建活动\n活动名：周末抽奖\n开始时间：2026-7-26 18:30\n结束时间：2026-7-26 20:30\n类型：抽奖\n说明：欢迎参加\n奖项：一等奖=1；二等奖=2\n参与群：123456789,2548761\n隐私：公开",
                "以上案例完整填写 8 项字段。发送时保持每个字段独占一行，不要把字段写在同一行。",
            ),
        ]
    return [
        (
            "必填定位字段",
            f"{prefix}修改活动\n活动ID：517",
            "活动ID 必填，用于指定活动，不会修改活动编号。",
        ),
        (
            "可修改字段（共 8 项）",
            "活动名\n开始时间\n结束时间\n类型\n说明\n奖项\n参与群\n隐私",
            "只填写需要变更的项；未填写或留空的字段保持原值。",
        ),
        (
            "修改案例",
            f"{prefix}修改活动\n活动ID：517\n活动名：发送“{prefix}报名517”参加变色龙团建活动\n结束时间：2026-7-26 23:59\n类型：通报\n说明：届时可以一起开黑。\n参与群：123456789,2548761\n隐私：公开",
            "进行中的活动仅超级管理员可修改；成功后会向参与群发送更新通知。",
        ),
    ]


def activity_entry_tutorial_text(operation: str, error: str) -> str:
    title = "创建活动新版录入教程" if operation == "create" else "修改活动新版录入教程"
    sections = activity_entry_tutorial_sections(operation)
    return f"本次录入未提交：{error}\n\n{title}（可复制版）\n\n" + "\n\n".join(
        f"{heading}\n{commands}\n说明：{note}" for heading, commands, note in sections
    )


async def send_activity_help_panel(
    bot: Bot,
    matcher: Any,
    event: MessageEvent,
    title: str,
    text: str,
    sections: list[tuple[str, str, str]],
    subtitle: str = "按角色整理的本地帮助",
) -> None:
    current = group_id(event)
    path = renderer.render_admin_panel(title, subtitle, sections)
    messages = build_forward_nodes([f"{title}图片", text], [path, None], bot.self_id, title=title)
    try:
        if current is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=messages,
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=current,
                messages=messages,
            )
    except Exception:
        logger.exception("Activity role help forward failed")
        await matcher.send(local_image_segment(path))
        await matcher.send(text)
    await matcher.finish()


async def send_activity_entry_tutorial(
    bot: Bot, matcher: Any, event: MessageEvent, operation: str, error: str
) -> None:
    title = "创建活动新版录入教程" if operation == "create" else "修改活动新版录入教程"
    await send_activity_help_panel(
        bot,
        matcher,
        event,
        title,
        activity_entry_tutorial_text(operation, error),
        activity_entry_tutorial_sections(operation),
        "新版字段录入  |  图片 + 可复制文字",
    )


activity_admin_help = on_command("活动管理员帮助", priority=5, block=True)


@activity_admin_help.handle()
async def _(bot: Bot, event: MessageEvent):
    if not service.is_event_activity_admin(event):
        await activity_admin_help.finish("只有活动管理员、活动有效群的群管理或超级管理员可以查看活动管理员帮助。")
    current = group_id(event)
    if current is not None and not service.allowed_group(current):
        await activity_admin_help.finish("当前群未开放跨群活动功能。")
    await send_activity_help_panel(
        bot,
        activity_admin_help,
        event,
        "活动管理员帮助",
        activity_admin_help_text(),
        activity_admin_help_sections(),
    )


def broadcast_notice(kind: str) -> str:
    if kind == "created":
        return "活动已发布"
    if kind.startswith("updated:"):
        return "活动信息已更新"
    return {
        "active": "活动已开始",
        "ended": "活动已结束",
        "cancelled": "活动已取消",
    }.get(kind, "活动通知")


def activity_action_hint(activity_id: int, status: str) -> str:
    prefix = settings.command_prefix
    if status in {"scheduled", "active"}:
        return (
            "活动开放群内可直接发送：\n"
            f"{prefix}报名{activity_id}    报名；\n"
            f"{prefix}活动详情{activity_id}  查看详情；\n"
            f"{prefix}查看名单{activity_id}  可查看当前参加人数和名单。"
        )
    return (
        f"ID 是 {activity_id}。活动开放群内可直接发送 {prefix}活动详情{activity_id} 查看最终信息。"
        f"发送 {prefix}查看名单 {activity_id} 可查看当前参加人数和名单。"
    )


def winner_view(activity: Any, rows: Sequence[Any], viewer_id: int, full: bool) -> list[dict[str, Any]]:
    public = str(activity["visibility"] or "masked") == "public"
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["display_user_id"] = (
            str(row["user_id"])
            if full or public
            else masked_user_id(int(row["user_id"]))
        )
        result.append(item)
    return result


def winner_pages(activity: Any, rows: Sequence[dict[str, Any]], avatar_paths: dict[int, Path] | None = None) -> list[Path]:
    chunks = [rows[index : index + 30] for index in range(0, len(rows), 30)] or [[]]
    return [
        renderer.render_activity_winners(
            activity_view(activity),
            chunk,
            avatar_paths=avatar_paths,
            page=index,
            total_pages=len(chunks),
        )
        for index, chunk in enumerate(chunks, 1)
    ]


def activity_broadcast_message(row: Any, bot: Bot | None = None):
    activity_id = int(row["activity_id"])
    activity = db.activity(activity_id)
    if activity is None:
        return None
    kind = str(row["kind"])
    if kind.startswith("unsupported:"):
        path = renderer.render_activity_unsupported_notice(str(activity["title"]))
        return local_image_segment(path)
    view = activity_view(activity)
    group_rows = activity_group_rows(activity_id)
    detail_path = renderer.render_activity_detail(
        view,
        group_rows,
        activity_prize_rows(activity_id),
        notice_text=broadcast_notice(kind),
        action_hint=activity_action_hint(activity_id, str(activity["status"])),
        group_avatar_paths=cached_activity_group_avatar_paths(group_rows),
    )
    if activity["status"] == "ended" and activity["activity_type"] == ACTIVITY_LOTTERY and bot:
        winner_rows = winner_view(activity, db.activity_winners(activity_id), 0, full=False)
        paths = [detail_path, *winner_pages(activity, winner_rows)]
        messages = [f"活动 #{activity_id} 最终详情"] + [
            f"活动 #{activity_id} 获奖名单，第 {index}/{len(paths) - 1} 页"
            for index in range(1, len(paths))
        ]
        return {
            "_activity_forward_messages": build_forward_nodes(
                messages, paths, bot.self_id, title="活动开奖结果"
            )
        }
    return local_image_segment(detail_path)


async def send_activity_image(matcher: Any, activity_id: int, kind: str) -> None:
    try:
        message = activity_broadcast_message({"activity_id": activity_id, "kind": kind})
        if message is not None:
            await matcher.send(message)
    except Exception:
        logger.exception("Activity local image send failed")


async def acknowledge_participation(
    bot: Bot, event: MessageEvent, *additional_emoji_ids: str
) -> None:
    """Add the participation acknowledgement reactions to the original group message."""
    if not isinstance(event, GroupMessageEvent):
        return
    emoji_ids = (settings.activity_ack_emoji_id, *additional_emoji_ids)
    for emoji_id in dict.fromkeys(emoji_ids):
        try:
            await call_qq_action(
                bot,
                "set_msg_emoji_like",
                message_id=int(event.message_id),
                emoji_id=emoji_id,
                set=True,
            )
        except Exception:
            logger.warning(
                "Activity reaction acknowledgement unavailable for message=%s emoji_id=%s",
                event.message_id,
                emoji_id,
            )


async def activity_maintenance(bot: Bot | None = None) -> None:
    service.refresh_due()
    target = bot
    if target is None:
        bots = get_bots()
        target = next(iter(bots.values()), None) if bots else None
    if target is not None:
        await service.deliver_broadcasts(target, activity_broadcast_message)


@driver.on_startup
async def _start_activity_scheduler() -> None:
    scheduler.add_job(
        activity_maintenance,
        "interval",
        seconds=settings.activity_maintenance_interval_seconds,
        id="activity-maintenance",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Cross-group activity scheduler started; interval=%ss",
        settings.activity_maintenance_interval_seconds,
    )


@driver.on_shutdown
async def _stop_activity_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


help_command = on_command("活动帮助", priority=5, block=True)


@help_command.handle()
async def _(bot: Bot, event: MessageEvent):
    current = await require_activity_catalog_context(help_command, event)
    text = activity_help()
    path = renderer.render_activity_help()
    try:
        if current is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=build_forward_nodes(
                    ["跨群活动帮助图片", text],
                    [path, None],
                    bot.self_id,
                    title="跨群活动帮助",
                ),
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=current,
                messages=build_forward_nodes(
                    ["跨群活动帮助图片", text],
                    [path, None],
                    bot.self_id,
                    title="跨群活动帮助",
                ),
            )
    except Exception:
        logger.exception("Activity help forward failed")
        await help_command.send(local_image_segment(path))
        await help_command.send(text)
    await help_command.finish()


hall = on_command("活动大厅", priority=5, block=True)


async def finish_activity_hall(matcher: Any, bot: Bot, event: MessageEvent) -> None:
    await require_activity_catalog_context(matcher, event)
    await activity_maintenance(bot)
    rows = [activity_view(row) for row in db.activities()]
    path = renderer.render_activity_hall(rows)
    fallback = "当前没有未开始或进行中的活动。" if not rows else "活动大厅已生成，请查看图片。"
    await send_local_image(matcher, path, fallback)


@hall.handle()
async def _(bot: Bot, event: MessageEvent):
    await finish_activity_hall(hall, bot, event)


detail = on_command("活动详情", priority=5, block=True)


@detail.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await activity_maintenance()
    activity_id = parse_activity_id(args.extract_plain_text())
    if activity_id is None:
        await detail.finish(f"用法：{settings.command_prefix}活动详情 ID")
    activity = db.activity(activity_id)
    if activity is None:
        await detail.finish("活动不存在。")
    _, full = await require_activity_view_context(detail, event, activity)
    await send_activity_detail_result(bot, detail, event, activity, full)


winners = on_command("获奖名单", priority=5, block=True)


async def handle_winner_command(bot: Bot, event: MessageEvent, args: Any, matcher: Any) -> None:
    activity_id = parse_activity_id(args.extract_plain_text())
    if activity_id is None:
        await matcher.finish(f"用法：{settings.command_prefix}获奖名单 ID")
    activity = db.activity(activity_id)
    if activity is None:
        await matcher.finish("活动不存在。")
    if activity["activity_type"] != ACTIVITY_LOTTERY or activity["status"] != "ended":
        await matcher.finish("该活动尚未产生获奖名单。")
    _, full = await require_activity_view_context(matcher, event, activity)
    await send_winner_result(bot, matcher, event, activity, full)


@winners.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_winner_command(bot, event, args, winners)


view_winners = on_command("查看获奖名单", priority=5, block=True)


@view_winners.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_winner_command(bot, event, args, view_winners)


create = on_command("创建活动", priority=5, block=True)


@create.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    current = await require_create_context(create, event)
    raw = args.extract_plain_text().strip()
    try:
        payload = parse_create_payload(raw)
        payload["groups_raw"] = ",".join(
            map(str, parse_group_ids(payload["groups_raw"], tuple(feature_scopes.groups("activity"))))
        )
        activity_id = service.create(event, payload, current)
    except (ValueError, TypeError) as exc:
        await send_activity_entry_tutorial(bot, create, event, "create", str(exc))
        return
    db.audit(int(event.user_id), "activity_create", current, f"activity_id={activity_id}")
    await service.deliver_broadcasts(bot, activity_broadcast_message)
    await send_activity_image(create, activity_id, "created")
    await create.finish()


edit = on_command("修改活动", priority=5, block=True)


@edit.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    try:
        activity_id, payload = parse_update_payload(args.extract_plain_text().strip())
    except ValueError as exc:
        await send_activity_entry_tutorial(bot, edit, event, "update", str(exc))
        return
    activity = db.activity(activity_id)
    if activity is None:
        await edit.finish("活动不存在。")
    current = await require_management_context(edit, event, activity)
    if not service.can_update(event, activity):
        if activity["status"] == "active":
            await edit.finish("只有超级管理员可以修改进行中的活动。")
        await edit.finish("活动已结束或取消，不能修改。")
    try:
        updated = service.update(event, activity_id, payload)
    except (ValueError, TypeError) as exc:
        await send_activity_entry_tutorial(bot, edit, event, "update", str(exc))
        return
    if not updated:
        await edit.finish("活动修改失败，可能已被其他操作更新。")
    db.audit(int(event.user_id), "activity_update", current, f"activity_id={activity_id}")
    await service.deliver_broadcasts(bot, activity_broadcast_message)
    if current is None:
        await send_activity_image(edit, activity_id, "updated:private")
    await edit.finish()


def parse_id(raw: str) -> tuple[int, str]:
    match = re.match(
        r"^[\s\u3000:：#]*(?:(?:id|编号)[\s\u3000:：#]*)?(\d{1,20})(?:[\s\u3000]+(.*))?$",
        raw.strip(),
        re.IGNORECASE | re.S,
    )
    if not match:
        raise ValueError("请提供活动 ID。")
    return int(match.group(1)), (match.group(2) or "").strip()


cancel = on_command("取消活动", priority=5, block=True)


@cancel.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    try:
        activity_id, reason = parse_id(args.extract_plain_text())
    except ValueError as exc:
        await cancel.finish(f"用法：{settings.command_prefix}取消活动 ID [原因]\n{exc}")
    activity = db.activity(activity_id)
    if activity is None:
        await cancel.finish("活动不存在。")
    current = await require_management_context(cancel, event, activity)
    if activity["status"] not in ACTIVE_STATUSES:
        await cancel.finish("当前活动不能取消。")
    if not service.cancel(activity_id, reason):
        await cancel.finish("活动取消失败，可能已被其他操作更新。")
    db.audit(int(event.user_id), "activity_cancel", current, f"activity_id={activity_id}")
    await service.deliver_broadcasts(bot, activity_broadcast_message)
    if current is None:
        await send_activity_image(cancel, activity_id, "cancelled")
    await cancel.finish()


finish = on_command("提前结束", priority=5, block=True)


@finish.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    try:
        activity_id, _ = parse_id(args.extract_plain_text())
    except ValueError as exc:
        await finish.finish(f"用法：{settings.command_prefix}提前结束 ID\n{exc}")
    activity = db.activity(activity_id)
    if activity is None:
        await finish.finish("活动不存在。")
    current = await require_management_context(finish, event, activity)
    if activity["status"] not in ACTIVE_STATUSES:
        await finish.finish("当前活动不能提前结束。")
    if not service.finish_now(activity):
        await finish.finish("活动结束失败，可能已被其他操作更新。")
    db.audit(int(event.user_id), "activity_finish", current, f"activity_id={activity_id}")
    await service.deliver_broadcasts(bot, activity_broadcast_message)
    if current is None:
        await send_activity_image(finish, activity_id, "ended")
    await finish.finish()


signup = on_command("报名", priority=5, block=True)


@signup.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    current = await require_group(signup, event)
    await activity_maintenance()
    activity_id = parse_activity_id(args.extract_plain_text())
    if activity_id is None:
        await signup.finish(f"用法：{settings.command_prefix}报名 ID")
    activity = db.activity(activity_id)
    if activity is None:
        await signup.finish("活动不存在。")
    await require_activity_access(signup, event, activity)
    result = db.register_activity(
        activity_id,
        int(event.user_id),
        current,
        sender_value(event, "nickname"),
        sender_value(event, "card"),
    )
    if result["status"] == "not_found":
        await signup.finish("活动不存在。")
    if result["status"] == "closed":
        await signup.finish("活动报名通道已关闭。")
    if result["status"] == "already":
        await acknowledge_participation(bot, event)
        await signup.finish()
    db.audit(int(event.user_id), "activity_signup", current, f"activity_id={activity_id}")
    await acknowledge_participation(bot, event)
    latest_activity = db.activity(activity_id)
    participant_count = int(latest_activity["participant_count"]) if latest_activity else 0
    await signup.finish(
        MessageSegment.reply(event.message_id)
        + f"成功报名参加 活动{activity_id}，当前参加人数 {participant_count}。"
    )


withdraw = on_command("取消报名", priority=5, block=True)


@withdraw.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    current = await require_group(withdraw, event)
    activity_id = parse_activity_id(args.extract_plain_text())
    if activity_id is None:
        await withdraw.finish(f"用法：{settings.command_prefix}取消报名 ID")
    activity = db.activity(activity_id)
    if activity is None:
        await withdraw.finish("活动不存在。")
    await require_activity_access(withdraw, event, activity)
    if activity["status"] not in ACTIVE_STATUSES:
        await withdraw.finish("活动已结束或取消，不能退出报名。")
    if not db.cancel_activity_registration(activity_id, int(event.user_id)):
        await withdraw.finish("你当前没有报名该活动。")
    db.audit(int(event.user_id), "activity_withdraw", current, f"activity_id={activity_id}")
    await acknowledge_participation(bot, event, settings.activity_withdraw_ack_emoji_id)
    await withdraw.finish()


async def activity_avatar_paths(rows: Sequence[dict[str, Any]]) -> dict[int, Path]:
    profiles = db.user_profiles(row["user_id"] for row in rows)
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["avatar_url"] = profiles.get(int(row["user_id"]), {}).get("avatar_url", "")
        enriched.append(item)
    cached = avatar_service.cached_paths(enriched)
    due = [
        row
        for row in enriched
        if int(row["user_id"]) not in cached or avatar_service.refresh_due(row)
    ]
    if due:
        await avatar_service.prefetch(due)
        cached = avatar_service.cached_paths(enriched)
    return cached


def participant_view(
    activity: Any,
    rows: Sequence[Any],
    viewer_group: int | None,
    full: bool,
) -> list[dict[str, Any]]:
    public = str(activity["visibility"] or "masked") == "public"
    result: list[dict[str, Any]] = []
    for row in rows:
        same_group = viewer_group is not None and int(row["source_group_id"]) == int(viewer_group)
        item = dict(row)
        item["display_user_id"] = str(row["user_id"]) if full or public or same_group else masked_user_id(int(row["user_id"]))
        item["group_label"] = str(row["group_name"] or row["source_group_id"]) if full or public or same_group else "其他活动群"
        result.append(item)
    return result


participants = on_command("查看名单", priority=5, block=True)


@participants.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    activity_id = parse_activity_id(args.extract_plain_text())
    if activity_id is None:
        await participants.finish(f"用法：{settings.command_prefix}查看名单 ID")
    activity = db.activity(activity_id)
    if activity is None:
        await participants.finish("活动不存在。")
    current, full = await require_activity_view_context(participants, event, activity)
    rows = db.activity_participants(
        activity_id, include_winners=str(activity["status"]) == "ended"
    )
    view_rows = participant_view(activity, rows, current, full)
    avatar_paths = await activity_avatar_paths(view_rows)
    chunks = [view_rows[index : index + 40] for index in range(0, len(view_rows), 40)] or [[]]
    paths: list[Path | None] = []
    messages: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        paths.append(
            renderer.render_activity_participants(
                {**dict(activity), "participant_count": len(rows)},
                chunk,
                avatar_paths=avatar_paths,
            )
        )
        messages.append(f"活动 #{activity_id} 报名名单，第 {index}/{len(chunks)} 页。")
    try:
        if current is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动报名名单"),
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=current,
                messages=build_forward_nodes(messages, paths, bot.self_id, title="活动报名名单"),
            )
    except Exception:
        logger.exception("Activity participant forward failed")
        for path in paths:
            await participants.send(local_image_segment(path))
    await participants.finish()


mine = on_command("我的活动", priority=5, block=True)


@mine.handle()
async def _(event: MessageEvent):
    if group_id(event) is None:
        if int(event.user_id) not in settings.operator_ids:
            await mine.finish("只有机器人所有者可以通过私聊查看活动记录。")
    else:
        await require_group(mine, event)
    rows = db.my_activities(int(event.user_id))
    if not rows:
        await mine.finish("你还没有参加过活动。")
    lines = ["我的活动："]
    for row in rows:
        result = f"中奖：{row['prize_name']}" if row["prize_name"] else "未中奖/未开奖"
        lines.append(
            f"#{row['activity_id']} {row['title']} · {service.state_label(row['status'])} · 报名序号 {row['registration_no']} · {result}"
        )
    await mine.finish("\n".join(lines))


@register_local_feature("activity_hall")
async def _run_local_activity_feature(
    matcher: Any,
    bot: Bot,
    event: Any,
    request: FeatureRequest,
) -> None:
    del request
    await finish_activity_hall(matcher, bot, event)
