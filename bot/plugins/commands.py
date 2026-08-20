from __future__ import annotations

import asyncio
import re
from math import ceil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.rule import Rule

from bot.application.admin_ui import (
    finish_admin_feedback,
    finish_group_overview,
    finish_with_image_or_text,
    group_avatar_paths,
    group_card_rows,
    report_renderer,
)
from bot.application.local_features import FeatureRequest, register_local_feature
from bot.application.command_helpers import (
    bounded_integer,
    current_group,
    is_operator,
    percent_value,
    text_arg,
    user_id,
    valid_qq_id,
)
from bot.config import settings
from bot.services.duplicate import (
    DUPLICATE_MODE,
    DUPLICATE_MODE_ALL,
    DUPLICATE_MODE_SOURCE,
    DuplicateService,
    render_duplicate_page,
    split_duplicate_message,
)
from bot.services.gateway import OneBotGateway
from bot.services.avatars import AvatarService
from bot.services.forward import build_forward_nodes
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action
from bot.services.roles import is_super_admin
from bot.services.runtime import database, passive_settings
from bot.services.stats import StatsService
from bot.services.whitelist_menu import (
    build_whitelist_menu_text,
    whitelist_menu_sections,
)


db = database()
passive = passive_settings()
duplicate_service = DuplicateService(db)
stats_service = StatsService(
    db,
    realtime_enabled=settings.stats_realtime_enabled,
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


@dataclass(slots=True)
class DuplicateSnapshot:
    created_at: float
    mode: DUPLICATE_MODE
    ignore_whitelist: bool
    group_ids: tuple[int, ...]
    result: list[dict[str, object]]
    avatar_paths: dict[int, Path]
    group_labels: tuple[tuple[int, str], ...]


DUPLICATE_SNAPSHOT_TTL = 300.0
DUPLICATE_IMAGE_MESSAGE_BATCH_SIZE = 8
duplicate_snapshots: dict[tuple[int, DUPLICATE_MODE, bool, tuple[int, ...]], DuplicateSnapshot] = {}
duplicate_scan_cooldowns: dict[tuple[DUPLICATE_MODE, bool, tuple[int, ...]], float] = {}
avatar_prefetch_tasks: set[asyncio.Task[object]] = set()


def prune_duplicate_snapshots() -> None:
    cutoff = monotonic() - DUPLICATE_SNAPSHOT_TTL
    for key, snapshot in tuple(duplicate_snapshots.items()):
        if snapshot.created_at < cutoff:
            duplicate_snapshots.pop(key, None)


def duplicate_scan_retry_after(
    mode: DUPLICATE_MODE, ignore_whitelist: bool, group_ids: tuple[int, ...]
) -> int:
    key = (mode, ignore_whitelist, group_ids)
    now = monotonic()
    available_at = duplicate_scan_cooldowns.get(key, 0.0)
    if available_at > now:
        return ceil(available_at - now)
    duplicate_scan_cooldowns[key] = now + settings.duplicate_scan_cooldown_seconds
    return 0


def group_allowed(event: MessageEvent) -> bool:
    group_id = current_group(event)
    return group_id is None or db.is_managed_group(group_id)


async def require_feature_group(
    matcher: object,
    event: MessageEvent,
    feature_key: str,
    feature_name: str,
) -> int | None:
    enabled_group_ids = passive.groups(feature_key)
    group_id = current_group(event)
    if group_id is None:
        if is_operator(event):
            return None
        await matcher.finish(f"请在已开启{feature_name}功能的群内使用。")  # type: ignore[attr-defined]
    if group_id not in enabled_group_ids:
        await matcher.finish(f"当前群未开启{feature_name}功能。")  # type: ignore[attr-defined]
    return group_id


def user_help_categories() -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """Group the visual manual by user workflow and merge equivalent commands."""
    prefix = settings.command_prefix
    categories = [
        (
            "使用说明",
            "",
            [
                ("图片帮助", f"{prefix}帮助", "查看完整的普通用户指令说明图片。"),
                ("折叠文字帮助", f"{prefix}帮助文字", "以合并转发发送可复制指令，不刷屏。"),
            ],
        ),
        (
            "直播与日程",
            "本功能由爱驼提供技术支持",
            [
                ("直播日程", f"{prefix}枝江直播 / {prefix}直播日程 / {prefix}本周直播", "查看本周直播日程。"),
                ("每日直播", f"{prefix}今日直播 / {prefix}明日直播", "查看今天或明天的 A-SOUL 直播日程。"),
            ],
        ),
        (
            "活动功能",
            "",
            [
                ("活动帮助", f"{prefix}活动帮助", "查看活动的专用使用说明。"),
                ("活动查看", f"{prefix}活动大厅 / {prefix}活动详情 <活动ID>", "查看可参与活动及指定活动详情。"),
                ("活动名单", f"{prefix}查看名单 <活动ID>", "查看指定活动的报名名单。"),
                ("活动获奖名单", f"{prefix}获奖名单 <活动ID> / {prefix}查看获奖名单 <活动ID>", "查看抽奖活动的获奖名单。"),
                ("活动参与", f"{prefix}报名 <活动ID> / {prefix}取消报名 <活动ID> / {prefix}我的活动", "报名、取消报名或查看自己的活动。"),
            ],
        ),
        (
            "今日老婆",
            "",
            [
                ("今日缘分", f"{prefix}今日老婆 / {prefix}今日缘分 / {prefix}强取 @群友 / {prefix}互动 靠近|倾听|回应|修复|助攻 [@群友]", "随机抽取或定向抽取今日关系，并在群像故事中选择下一步行动。"),
                ("缘分档案", f"{prefix}我的缘分 / {prefix}群缘分 / {prefix}群缘分 历史 / {prefix}离婚", "查看当前关系、永久个人留档、群内往日摘要，或结束今日关系。"),
            ],
        ),
        (
            "小游戏",
            "",
            [
                ("小游戏菜单", f"{prefix}游戏列表 / {prefix}小游戏列表", "查看小游戏玩法和入口。"),
                ("俄罗斯转盘", f"{prefix}装填 / {prefix}开枪", "发起或进行俄罗斯转盘。"),
                ("定时炸弹", f"{prefix}装弹 / {prefix}丢给 @成员", "发起或传递定时炸弹。"),
                ("成语炸弹", f"{prefix}装弹成语 [专业/娱乐] [60-600]\n四字词 {prefix}丢给 @成员", "发起或传递成语接龙炸弹。"),
                ("幸运骰局", f"{prefix}骰子", "发起一局幸运骰局。"),
                ("猜数字", f"{prefix}猜数 / {prefix}猜 <0-999>", "发起猜数字或提交猜测。"),
            ],
        ),
        (
            "小游戏榜单",
            "",
            [
                ("群游戏榜单", f"{prefix}转盘榜 / {prefix}炸弹榜 / {prefix}骰子榜 / {prefix}猜数榜", "查看当前群游戏榜单。"),
                ("总游戏榜单", f"{prefix}转盘总榜 / {prefix}炸弹总榜 / {prefix}骰子总榜 / {prefix}猜数总榜", "查看所有已开启游戏群的榜单。"),
            ],
        ),
    ]
    if settings.stats_realtime_enabled:
        categories.append(
            (
                "A 海岸发言统计",
                "",
                [
                    ("当前群发言排行", f"{prefix}发言排行 / {prefix}发言榜 / {prefix}统计 [日/周/月/总]", "查看当前 A 海岸群的发言排行。"),
                    ("A 海岸发言排行", f"{prefix}A海岸发言排行 / {prefix}A海岸发言榜 / {prefix}A海岸统计 [日/周/月/总]", "合并查看五个 A 海岸群的排行。"),
                    ("发言画像", f"{prefix}发言画像 <QQ号|@成员> / {prefix}画像 <QQ号|@成员>", "生成指定成员的 A 海岸发言画像。"),
                ],
            )
        )
    return categories


def user_help_sections() -> list[tuple[str, str, str]]:
    """Flatten visual categories for callers that only need command entries."""
    return [entry for _, _, entries in user_help_categories() for entry in entries]


def user_help_text() -> str:
    """Return every copyable public command, including compatibility aliases."""
    prefix = settings.command_prefix
    commands = [
        f"{prefix}帮助", f"{prefix}帮助文字",
        f"{prefix}枝江直播 [状态]", f"{prefix}直播日程 [状态]",
        f"{prefix}今日直播", f"{prefix}明日直播", f"{prefix}本周直播",
        f"{prefix}活动帮助", f"{prefix}活动大厅", f"{prefix}活动详情 <活动ID>",
        f"{prefix}查看名单 <活动ID>", f"{prefix}获奖名单 <活动ID>",
        f"{prefix}查看获奖名单 <活动ID>", f"{prefix}报名 <活动ID>",
        f"{prefix}取消报名 <活动ID>", f"{prefix}我的活动",
        f"{prefix}游戏列表", f"{prefix}小游戏列表", f"{prefix}装填", f"{prefix}开枪",
        f"{prefix}装弹", f"{prefix}丢给 @成员", f"{prefix}装弹成语 [专业/娱乐] [60-600]",
        f"四字词 {prefix}丢给 @成员", f"{prefix}骰子", f"{prefix}猜数", f"{prefix}猜 <0-999>",
        f"{prefix}今日老婆", f"{prefix}今日缘分", f"{prefix}强取 @群友", f"{prefix}我的缘分", f"{prefix}我的老婆", f"{prefix}群缘分", f"{prefix}群老婆", f"{prefix}离婚", f"{prefix}解缘",
        f"{prefix}转盘榜", f"{prefix}转盘总榜", f"{prefix}俄罗斯转盘榜单",
        f"{prefix}俄罗斯转盘总榜单", f"{prefix}炸弹榜", f"{prefix}炸弹总榜",
        f"{prefix}定时炸弹榜单", f"{prefix}定时炸弹总榜单", f"{prefix}骰子榜",
        f"{prefix}骰子总榜", f"{prefix}幸运骰局榜单", f"{prefix}幸运骰局总榜单",
        f"{prefix}猜数榜", f"{prefix}猜数总榜", f"{prefix}猜数字榜单", f"{prefix}猜数字总榜单",
    ]
    if settings.stats_realtime_enabled:
        commands.extend((
            f"{prefix}发言排行 [日/周/月/总]", f"{prefix}发言榜 [日/周/月/总]", f"{prefix}统计 [日/周/月/总]",
            f"{prefix}A海岸发言排行 [日/周/月/总]", f"{prefix}A海岸发言榜 [日/周/月/总]", f"{prefix}A海岸统计 [日/周/月/总]",
            f"{prefix}a海岸发言排行 [日/周/月/总]", f"{prefix}a海岸发言榜 [日/周/月/总]", f"{prefix}a海岸统计 [日/周/月/总]",
            f"{prefix}发言画像 <QQ号|@成员>", f"{prefix}画像 <QQ号|@成员>",
        ))
    return "\n".join(commands)


def user_help_text_forward_nodes(bot_id: int | str) -> list[dict[str, Any]]:
    return build_forward_nodes(
        [user_help_text()], [None], bot_id, title="普通用户帮助文字"
    )


def user_help_image_message(path: Path) -> MessageSegment:
    """Keep the standard help reply to a single image segment."""
    return local_image_segment(path)


async def send_user_help_image(matcher: Any) -> None:
    """Render first, then finish outside the rendering error boundary."""
    try:
        path = report_renderer.render_user_help(
            "普通用户帮助",
            "按功能分类；文字版请发送 #帮助文字",
            user_help_categories(),
        )
    except Exception:
        logger.exception("User help image failed")
        await matcher.finish("帮助图片生成失败，请稍后再试。")
        return
    await matcher.finish(user_help_image_message(path))


user_help = on_command("帮助", priority=5, block=True)


@user_help.handle()
async def _():
    await send_user_help_image(user_help)


user_help_text_command = on_command("帮助文字", priority=5, block=True)


@user_help_text_command.handle()
async def _(bot: Bot, event: MessageEvent):
    try:
        group_id = current_group(event)
        if group_id is None:
            await call_qq_action(
                bot,
                "send_private_forward_msg",
                user_id=int(event.user_id),
                messages=user_help_text_forward_nodes(bot.self_id),
            )
        else:
            await call_qq_action(
                bot,
                "send_group_forward_msg",
                group_id=group_id,
                messages=user_help_text_forward_nodes(bot.self_id),
            )
    except Exception:
        logger.exception("User help text forward failed")
        await user_help_text_command.finish("帮助文字折叠发送失败，请稍后再试。")
    await user_help_text_command.finish()


def user_report_rows(rows: list[Any]) -> list[dict[str, Any]]:
    """Add the latest local nickname/avatar metadata to user-facing rows."""
    normalized = [dict(row) for row in rows]
    profiles = db.user_profiles(row["user_id"] for row in normalized)
    for row in normalized:
        profile = profiles.get(int(row["user_id"]), {})
        if not row.get("nickname"):
            row["nickname"] = profile.get("nickname", "")
        row["avatar_url"] = row.get("avatar_url") or profile.get("avatar_url", "")
    return normalized


async def cached_avatar_paths(rows: list[dict[str, Any]]) -> dict[int, Path]:
    if settings.report_output_mode != "local_image":
        return {}
    cached = avatar_service.cached_paths(rows)
    due = [
        row
        for row in rows
        if int(row["user_id"]) not in cached or avatar_service.refresh_due(row)
    ]
    if due:
        task = asyncio.create_task(avatar_service.prefetch(due))
        avatar_prefetch_tasks.add(task)
        task.add_done_callback(avatar_prefetch_tasks.discard)
    return cached


async def finish_whitelist_forward(
    bot: Bot,
    matcher: object,
    event: MessageEvent,
    title: str,
    fallback: str,
    render: Callable[[], Path],
) -> None:
    """Send one client-supported whitelist image, with text only as fallback."""
    if settings.report_output_mode != "local_image":
        await matcher.finish(fallback)  # type: ignore[attr-defined]
        return
    try:
        path = render()
    except Exception:
        logger.exception("Whitelist image rendering failed; using text output")
        await matcher.finish(fallback)  # type: ignore[attr-defined]
        return

    try:
        await matcher.send(local_image_segment(path))  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Whitelist image response failed; using text output")
        await matcher.finish(fallback)  # type: ignore[attr-defined]
        return
    await matcher.finish()  # type: ignore[attr-defined]


async def finish_whitelist_change(
    bot: Bot,
    matcher: object,
    event: MessageEvent,
    row: dict[str, Any],
    detail: str,
) -> None:
    nickname = str(row.get("nickname") or "未获取")
    fallback = f"{row['user_id']}（{nickname}）{detail}"
    avatar_paths = await cached_avatar_paths([row])
    await finish_whitelist_forward(
        bot,
        matcher,
        event,
        "白名单更新",
        fallback,
        lambda: report_renderer.render_whitelist(
            [row],
            avatar_paths,
            title="白名单更新",
            subtitle="本地用户信息",
        ),
    )


async def finish_whitelist_list(
    bot: Bot,
    matcher: object,
    event: MessageEvent,
    rows: list[dict[str, Any]],
) -> None:
    fallback = "查重白名单为空。" if not rows else "查重白名单：\n" + "\n".join(
        f"{row['user_id']}（{row['nickname'] or '未获取'}）：{row['note']}" for row in rows
    )
    avatar_paths = await cached_avatar_paths(rows)
    await finish_whitelist_forward(
        bot,
        matcher,
        event,
        "查重白名单",
        fallback,
        lambda: report_renderer.render_whitelist(
            rows,
            avatar_paths,
            title="查重白名单",
            subtitle=f"共 {len(rows)} 人",
        ),
    )


async def finish_whitelist_menu(bot: Bot, matcher: object, event: MessageEvent) -> None:
    """Send the whitelist menu as one folded image-and-text message."""
    await finish_whitelist_forward(
        bot,
        matcher,
        event,
        "白名单操作菜单",
        build_whitelist_menu_text(settings.command_prefix),
        lambda: report_renderer.render_admin_panel(
            "白名单操作菜单",
            "本地图片菜单，不调用网络大模型",
            whitelist_menu_sections(settings.command_prefix),
        ),
    )


async def send_duplicate_pages(
    bot: Bot,
    event: MessageEvent,
    matcher: object,
    page_messages: list[str],
    page_paths: list[Path | None],
) -> None:
    """Send image pages as compact, client-supported QQ multi-image messages."""
    image_message = Message()
    for message, path in zip(page_messages, page_paths):
        if path:
            try:
                image_message += local_image_segment(path)
                if len(image_message) >= DUPLICATE_IMAGE_MESSAGE_BATCH_SIZE:
                    await matcher.send(image_message)  # type: ignore[attr-defined]
                    image_message = Message()
                continue
            except Exception:
                logger.exception("Duplicate report image failed; using text fallback")
        if image_message:
            await matcher.send(image_message)  # type: ignore[attr-defined]
            image_message = Message()
        for chunk in split_duplicate_message(message):
            await matcher.send(chunk)  # type: ignore[attr-defined]
    if image_message:
        await matcher.send(image_message)  # type: ignore[attr-defined]
    await matcher.finish()  # type: ignore[attr-defined]


def parse_page(raw: str) -> tuple[str, int]:
    match = re.search(r"(?:页|page)\s*(\d+)", raw, re.IGNORECASE)
    page = max(1, int(match.group(1))) if match else 1
    return (raw[: match.start()] + raw[match.end() :] if match else raw).strip(), page


def selected_groups(
    raw: str, mode: DUPLICATE_MODE, command_name: str
) -> tuple[tuple[int, ...], str | None]:
    ids = tuple(dict.fromkeys(int(value) for value in re.findall(r"(?<!\d)\d{4,20}(?!\d)", raw)))
    configured = set(passive.groups("duplicate"))
    if not ids:
        ids = settings.managed_order(passive.groups("duplicate"))
    if not ids:
        return ids, "还没有配置管理群。"
    invalid = [str(group_id) for group_id in ids if group_id not in configured]
    if invalid:
        return ids, f"这些群未开启查重功能：{', '.join(invalid)}"
    if not 2 <= len(ids) <= 10:
        if mode == DUPLICATE_MODE_ALL:
            return ids, (
                f"用法：{settings.command_prefix}{command_name} 群号1 群号2 "
                f"[群号3 ...]，至少选择 2 个群。"
            )
        return ids, (
            f"用法：{settings.command_prefix}{command_name} 起点群号 目标群号1 "
            f"[目标群号2 ...]，至少选择 1 个起点群和 1 个目标群。"
        )
    return ids, None


async def handle_duplicate_command(
    matcher: object,
    bot: Bot,
    event: MessageEvent,
    args: object,
    mode: DUPLICATE_MODE,
    command_name: str,
    ignore_whitelist: bool = False,
) -> None:
    await require_feature_group(matcher, event, "duplicate", "查重")
    if not is_operator(event):
        await matcher.finish("没有执行跨群查重的权限。")  # type: ignore[attr-defined]
    raw = text_arg(args)
    base_raw, page = parse_page(raw)
    group_ids, error = selected_groups(base_raw, mode, command_name)
    if error:
        await matcher.finish(error)  # type: ignore[attr-defined]
    if not group_ids:
        await matcher.finish("还没有配置管理群。")  # type: ignore[attr-defined]
    if page == 1:
        retry_after = duplicate_scan_retry_after(mode, ignore_whitelist, group_ids)
        if retry_after:
            await matcher.finish(f"查重请求过于频繁，请 {retry_after} 秒后再试。")  # type: ignore[attr-defined]
    group_rows = {int(row["group_id"]): str(row["group_name"] or "") for row in db.managed_groups()}
    group_labels = tuple((group_id, group_rows.get(group_id, "")) for group_id in group_ids)
    prune_duplicate_snapshots()
    snapshot_key = (user_id(event), mode, ignore_whitelist, group_ids)
    snapshot = duplicate_snapshots.get(snapshot_key)
    if page == 1:
        if mode == DUPLICATE_MODE_ALL:
            result, failed = await duplicate_service.scan_all(
                OneBotGateway(bot), group_ids, ignore_whitelist=ignore_whitelist
            )
        else:
            result, failed = await duplicate_service.scan(
                OneBotGateway(bot), group_ids, ignore_whitelist=ignore_whitelist
            )
        if failed:
            await matcher.finish("查重未完成，无法读取群成员：" + ", ".join(map(str, failed)))  # type: ignore[attr-defined]
        avatar_paths = await cached_avatar_paths(result)
        snapshot = DuplicateSnapshot(
            monotonic(), mode, ignore_whitelist, group_ids, result, avatar_paths, group_labels
        )
        duplicate_snapshots[snapshot_key] = snapshot
        db.audit(
            user_id(event),
            "duplicate_scan",
            detail=(
                f"mode=all;ignore_whitelist={ignore_whitelist};groups={','.join(map(str, group_ids))}"
                if mode == DUPLICATE_MODE_ALL
                else f"mode=source;ignore_whitelist={ignore_whitelist};source={group_ids[0]};targets={','.join(map(str, group_ids[1:]))}"
            ),
        )
    elif snapshot is None:
        await matcher.finish("查重快照不存在或已过期，请重新执行查重。")  # type: ignore[attr-defined]

    page_messages: list[str] = []
    page_paths: list[Path | None] = []
    try:
        message, _ = render_duplicate_page(
            snapshot.result,
            page,
            settings.command_prefix,
            snapshot.group_labels,  # type: ignore[union-attr]
            scope_mode=mode,
            command_name=command_name,
            page_size=None,
        )
    except ValueError as exc:
        await matcher.finish(str(exc))  # type: ignore[attr-defined]
    page_messages.append(message)
    path: Path | None = None
    if settings.report_output_mode == "local_image":
        try:
            path = report_renderer.render_duplicate(
                snapshot.result,  # type: ignore[union-attr]
                len(snapshot.result),  # type: ignore[union-attr]
                1,
                1,
                snapshot.avatar_paths,  # type: ignore[union-attr]
                snapshot.group_labels,  # type: ignore[union-attr]
                comparison_mode=mode,
            )
        except Exception:
            logger.exception("Local duplicate report rendering failed; using text fallback")
    page_paths.append(path)
    await send_duplicate_pages(bot, event, matcher, page_messages, page_paths)


duplicate_all = on_command("查重1", priority=5, block=True)


@duplicate_all.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_duplicate_command(duplicate_all, bot, event, args, DUPLICATE_MODE_ALL, "查重1")


duplicate_all_ignore_whitelist = on_command("查重3", priority=5, block=True)


@duplicate_all_ignore_whitelist.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_duplicate_command(
        duplicate_all_ignore_whitelist,
        bot,
        event,
        args,
        DUPLICATE_MODE_ALL,
        "查重3",
        ignore_whitelist=True,
    )


duplicate_source = on_command("查重2", priority=5, block=True)


@duplicate_source.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_duplicate_command(duplicate_source, bot, event, args, DUPLICATE_MODE_SOURCE, "查重2")


duplicate_source_ignore_whitelist = on_command("查重4", priority=5, block=True)


@duplicate_source_ignore_whitelist.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_duplicate_command(
        duplicate_source_ignore_whitelist,
        bot,
        event,
        args,
        DUPLICATE_MODE_SOURCE,
        "查重4",
        ignore_whitelist=True,
    )


# Keep the previous command as an alias for the source/target mode.
duplicate = on_command("查重", priority=5, block=True)


@duplicate.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await handle_duplicate_command(duplicate, bot, event, args, DUPLICATE_MODE_SOURCE, "查重2")


async def require_passive_settings_operator(matcher: object, event: MessageEvent) -> None:
    if not is_operator(event):
        await matcher.finish("没有维护被动互动参数的权限。")  # type: ignore[attr-defined]


def passive_status_text(group_id: int) -> str:
    current = passive.for_group(group_id)
    return (
        f"随机表情：{current.reaction_probability * 100:g}% 命中，"
        f"每群冷却 {current.reaction_cooldown_seconds} 秒。\n"
        f"随机复读：{current.repeat_probability * 100:g}% 命中，"
        f"冷却 {current.repeat_cooldown_seconds // 60} 分钟，"
        f"消息间隔 {current.repeat_message_interval} 条。\n"
        f"三连复读：{'已开启' if current.triple_repeat_enabled else '已关闭'}，"
        f"命中概率 {current.triple_repeat_probability * 100:g}%，"
        "连续三条相同文本后按概率额外复读一次，不占用随机复读冷却。\n"
        "建议最少：表情冷却 10 秒；复读冷却 15 分钟、消息间隔 50 条。"
    )


def all_passive_status_text() -> str:
    rows = [passive_status_text(group_id) for group_id in sorted(passive.group_ids)]
    return "\n\n".join(rows) if rows else "当前没有被动互动群。"


def group_scope_detail(group_id: int) -> str:
    features = (
        ("duplicate", "查重"),
        ("game", "游戏"),
        ("game_api", "游戏接口"),
        ("activity", "活动"),
        ("passive", "被动互动"),
        ("hourly", "整点报时"),
    )
    return "｜".join(
        f"{label}：{'开' if group_id in passive.groups(feature) else '关'}"
        for feature, label in features
    )


async def finish_passive_group_feedback(
    matcher: object,
    title: str,
    subtitle: str,
    group_ids: Iterable[int],
) -> None:
    ids = tuple(group_ids)
    rows = await group_card_rows(
        ids,
        {group_id: passive_status_text(group_id) for group_id in ids},
        {group_id: "被动互动" for group_id in ids},
    )
    await finish_group_overview(matcher, title, subtitle, rows)


def passive_target_group(tokens: list[str]) -> int | None:
    if not tokens:
        return None
    group_id = valid_qq_id(tokens[0])
    return group_id if group_id in passive.group_ids else None


reaction_probability = on_command("表情命中率", priority=5, block=True)


@reaction_probability.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(reaction_probability, event)
    tokens = text_arg(args).split()
    group_id = passive_target_group(tokens)
    value = percent_value(tokens[1], 0.5) if len(tokens) == 2 else None
    if group_id is None or value is None:
        await reaction_probability.finish("用法：#表情命中率 QQ群号 0-50%")
    passive.set_reaction_probability(group_id, value)
    db.audit(user_id(event), "reaction_probability_update", current_group(event), detail=f"group={group_id};value={value}")
    await finish_passive_group_feedback(reaction_probability, "随机表情已更新", f"仅群 {group_id} 命中率已更新", (group_id,))


reaction_cooldown = on_command("表情冷却", priority=5, block=True)


@reaction_cooldown.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(reaction_cooldown, event)
    tokens = text_arg(args).split()
    group_id = passive_target_group(tokens)
    value = bounded_integer(tokens[1], 0, 3600) if len(tokens) == 2 else None
    if group_id is None or value is None:
        await reaction_cooldown.finish("用法：#表情冷却 QQ群号 0-3600（秒；建议最少 10 秒）")
    passive.set_reaction_cooldown_seconds(group_id, value)
    db.audit(user_id(event), "reaction_cooldown_update", current_group(event), detail=f"group={group_id};value={value}")
    await finish_passive_group_feedback(reaction_cooldown, "随机表情已更新", f"仅群 {group_id} 冷却已更新", (group_id,))


repeat_probability = on_command("复读命中率", priority=5, block=True)


@repeat_probability.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(repeat_probability, event)
    tokens = text_arg(args).split()
    group_id = passive_target_group(tokens)
    value = percent_value(tokens[1], 0.1) if len(tokens) == 2 else None
    if group_id is None or value is None:
        await repeat_probability.finish("用法：#复读命中率 QQ群号 0-10%")
    passive.set_repeat_probability(group_id, value)
    db.audit(user_id(event), "repeat_probability_update", current_group(event), detail=f"group={group_id};value={value}")
    await finish_passive_group_feedback(repeat_probability, "随机复读已更新", f"仅群 {group_id} 命中率已更新", (group_id,))


repeat_cooldown = on_command("复读冷却", priority=5, block=True)


@repeat_cooldown.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(repeat_cooldown, event)
    tokens = text_arg(args).split()
    group_id = passive_target_group(tokens)
    minutes = bounded_integer(tokens[1], 0, 1440) if len(tokens) == 2 else None
    if group_id is None or minutes is None:
        await repeat_cooldown.finish("用法：#复读冷却 QQ群号 0-1440（分钟；建议最少 15 分钟）")
    passive.set_repeat_cooldown_seconds(group_id, minutes * 60)
    db.audit(user_id(event), "repeat_cooldown_update", current_group(event), detail=f"group={group_id};value={minutes * 60}")
    await finish_passive_group_feedback(repeat_cooldown, "随机复读已更新", f"仅群 {group_id} 冷却已更新", (group_id,))


repeat_interval = on_command("复读间隔", priority=5, block=True)


@repeat_interval.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(repeat_interval, event)
    tokens = text_arg(args).split()
    group_id = passive_target_group(tokens)
    value = bounded_integer(tokens[1], 0, 10000) if len(tokens) == 2 else None
    if group_id is None or value is None:
        await repeat_interval.finish("用法：#复读间隔 QQ群号 0-10000（条消息；建议最少 50 条）")
    passive.set_repeat_message_interval(group_id, value)
    db.audit(user_id(event), "repeat_interval_update", current_group(event), detail=f"group={group_id};value={value}")
    await finish_passive_group_feedback(repeat_interval, "随机复读已更新", f"仅群 {group_id} 消息间隔已更新", (group_id,))


triple_repeat = on_command("三连复读", priority=5, block=True)


@triple_repeat.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(triple_repeat, event)
    tokens = text_arg(args).split()
    if not tokens or tokens[0] in {"列表", "list"}:
        rows = await group_card_rows(
            passive.group_ids,
            {
                group_id: (
                    f"三连复读：{'已开启' if config.triple_repeat_enabled else '已关闭'}，"
                    f"命中概率 {config.triple_repeat_probability * 100:g}%"
                )
                for group_id, config in passive.group_settings()
            },
            {group_id: "三连复读" for group_id in passive.group_ids},
        )
        await finish_group_overview(triple_repeat, "三连复读", "各被动互动群的独立开关和命中概率", rows)
    group_id = passive_target_group(tokens)
    action = tokens[1].lower() if len(tokens) == 2 else ""
    if group_id is None:
        await triple_repeat.finish("用法：#三连复读 QQ群号 开启|关闭|状态|概率 0-100%")
    if len(tokens) == 3 and action in {"概率", "probability"}:
        value = percent_value(tokens[2], 1.0)
        if value is None:
            await triple_repeat.finish("用法：#三连复读 QQ群号 概率 0-100%")
        passive.set_triple_repeat_probability(group_id, value)
        db.audit(
            user_id(event),
            "triple_repeat_probability_update",
            current_group(event),
            detail=f"group={group_id};value={value}",
        )
        await finish_passive_group_feedback(
            triple_repeat,
            "三连复读已更新",
            f"群 {group_id} 命中概率已更新为 {value * 100:g}%",
            (group_id,),
        )
        return
    if action not in {"开启", "关闭", "状态", "on", "off", "status"}:
        await triple_repeat.finish("用法：#三连复读 QQ群号 开启|关闭|状态|概率 0-100%")
    if action in {"状态", "status"}:
        await finish_passive_group_feedback(
            triple_repeat,
            "三连复读",
            (
                f"群 {group_id}"
                f"{'已开启' if passive.for_group(group_id).triple_repeat_enabled else '已关闭'}，"
                f"命中概率 {passive.for_group(group_id).triple_repeat_probability * 100:g}%"
            ),
            (group_id,),
        )
        return
    enabled = action in {"开启", "on"}
    passive.set_triple_repeat_enabled(group_id, enabled)
    db.audit(user_id(event), "triple_repeat_update", current_group(event), detail=f"group={group_id};value={str(enabled).lower()}")
    await finish_passive_group_feedback(triple_repeat, "三连复读已更新", f"群 {group_id}{'已开启' if enabled else '已关闭'}", (group_id,))


passive_status = on_command("被动互动状态", priority=5, block=True)


@passive_status.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(passive_status, event)
    tokens = text_arg(args).split()
    if len(tokens) > 1 or (tokens and (group_id := passive_target_group(tokens)) is None):
        await passive_status.finish("用法：#被动互动状态 [QQ群号]")
    group_ids = (group_id,) if tokens else passive.group_ids
    await finish_passive_group_feedback(passive_status, "被动互动状态", "当前生效的按群持久化配置", group_ids)


passive_group = on_command("被动互动群", priority=5, block=True)


@passive_group.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(passive_group, event)
    tokens = text_arg(args).split()
    if not tokens or tokens[0] in {"列表", "list"}:
        groups = sorted(passive.group_ids)
        rows = await group_card_rows(
            groups,
            {group_id: passive_status_text(group_id) for group_id in groups},
            {group_id: "被动互动" for group_id in groups},
        )
        await finish_group_overview(passive_group, "被动互动群", f"共 {len(groups)} 个群", rows)
    if len(tokens) != 2 or (group_id := valid_qq_id(tokens[1])) is None:
        await passive_group.finish("用法：#被动互动群 添加|移除|列表 QQ群号")
    action = tokens[0].lower()
    if action in {"添加", "add"}:
        try:
            passive.add_group(group_id)
        except ValueError:
            await passive_group.finish("该群未在机器人管理范围内，不能加入被动互动群。")
        db.audit(user_id(event), "passive_group_add", current_group(event), detail=str(group_id))
        await finish_passive_group_feedback(passive_group, "被动互动群已更新", f"已加入群 {group_id}", (group_id,))
    if action in {"移除", "删除", "remove", "delete"}:
        passive.remove_group(group_id)
        db.audit(user_id(event), "passive_group_remove", current_group(event), detail=str(group_id))
        rows = await group_card_rows((group_id,), {group_id: "已移出被动互动范围。"}, {group_id: "已移除"})
        await finish_group_overview(passive_group, "被动互动群已更新", f"已移除群 {group_id}", rows)
    await passive_group.finish("用法：#被动互动群 添加|移除|列表 QQ群号")


FEATURE_SCOPE_ALIASES = {
    "查重": ("duplicate", "查重"),
    "游戏": ("game", "游戏"),
    "游戏接口": ("game_api", "游戏接口"),
    "今日老婆": ("today_wife", "今日老婆"),
    "缘分": ("today_wife", "今日老婆"),
    "活动": ("activity", "活动"),
    "被动": ("passive", "被动互动"),
    "被动互动": ("passive", "被动互动"),
    "整点报时": ("hourly", "整点报时"),
    "整点": ("hourly", "整点报时"),
}


feature_scope = on_command("功能范围", priority=5, block=True)


@feature_scope.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await require_passive_settings_operator(feature_scope, event)
    tokens = text_arg(args).split()
    if not tokens or tokens[0] in {"列表", "list"}:
        group_ids = tuple(settings.managed_group_ids)
        rows = await group_card_rows(
            group_ids,
            {group_id: group_scope_detail(group_id) for group_id in group_ids},
            {group_id: "功能范围" for group_id in group_ids},
        )
        await finish_group_overview(feature_scope, "功能范围", "已持久化的业务范围", rows)
    if len(tokens) != 3 or tokens[0] not in FEATURE_SCOPE_ALIASES:
        await feature_scope.finish(
            "用法：#功能范围 查重|游戏|游戏接口|今日老婆|缘分|活动|被动|整点报时 添加|移除 QQ群号"
        )
    feature, label = FEATURE_SCOPE_ALIASES[tokens[0]]
    action = tokens[1].lower()
    group_id = valid_qq_id(tokens[2])
    if group_id is None:
        await feature_scope.finish("QQ群号必须是数字。")
    if action in {"添加", "add"}:
        try:
            groups = passive.add_feature_group(feature, group_id)
        except ValueError:
            await feature_scope.finish("该群未在机器人管理范围内，不能加入功能范围。")
        db.audit(user_id(event), "feature_scope_add", current_group(event), f"feature={feature};group={group_id}")
        rows = await group_card_rows(
            groups,
            {item: group_scope_detail(item) for item in groups},
            {item: label for item in groups},
        )
        await finish_group_overview(feature_scope, "功能范围已更新", f"{label}功能已加入群 {group_id}", rows)
    if action in {"移除", "删除", "remove", "delete"}:
        groups = passive.remove_feature_group(feature, group_id)
        db.audit(user_id(event), "feature_scope_remove", current_group(event), f"feature={feature};group={group_id}")
        rows = await group_card_rows(
            (group_id,),
            {group_id: group_scope_detail(group_id)},
            {group_id: f"已移除{label}"},
        )
        await finish_group_overview(feature_scope, "功能范围已更新", f"{label}功能已移除群 {group_id}", rows)
    await feature_scope.finish(
        "用法：#功能范围 查重|游戏|游戏接口|今日老婆|缘分|活动|被动|整点报时 添加|移除 QQ群号"
    )


admin_help = on_command(
    "管理员帮助",
    aliases={"超级管理员帮助"},
    priority=5,
    block=True,
)


def admin_help_page_text(title: str, sections: list[tuple[str, str, str]]) -> str:
    return title + "\n\n" + "\n\n".join(
        f"【{heading}】\n{commands}\n说明：{note}".strip()
        for heading, commands, note in sections
    )


def super_admin_help_pages() -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """Build the complete, current super-admin manual as small readable pages."""
    prefix = settings.command_prefix
    scope_lines = "\n".join(
        f"{label}：{','.join(map(str, sorted(passive.groups(feature)))) or '未开启'}"
        for feature, label in (
            ("duplicate", "查重群"),
            ("game", "游戏群"),
            ("game_api", "游戏接口群"),
            ("today_wife", "今日老婆群"),
            ("activity", "活动群"),
            ("passive", "被动互动群"),
            ("hourly", "整点报时群"),
        )
    )
    managed = ",".join(map(str, sorted(settings.managed_group_ids)))
    return [
        (
            "超级管理员手册 1/8｜权限与触发",
            "权限、范围、@ 规则和费用边界",
            [
                (
                    "三类用户",
                    "超级管理员：BOT_OPERATOR_IDS\n活动管理员：ACTIVITY_ADMIN_IDS，或活动有效群的群主、群管理员\n普通用户：未列入以上名单的 QQ",
                    "活动有效群的群主和群管理员自动获得活动管理权限，ACTIVITY_ADMIN_BLACKLIST_IDS 可排除指定 QQ。活动管理员可创建活动并管理自己创建的活动；超级管理员可管理全部活动和全局配置。",
                ),
                (
                    "触发规则",
                    "所有角色：使用 #指令 [参数]\n普通用户、活动管理员和超级管理员共用同一命令格式\n私聊同样使用 # 前缀",
                    "命令先识别 # 前缀，再按已有权限规则决定是否执行。机器人自身消息不会再次触发。",
                ),
                (
                    "范围与费用",
                    f"管理群总范围：{managed}\n管理群数量：{len(settings.managed_group_ids)}/10\n传输：{settings.transport}，接口：{settings.host}:{settings.port}",
                    "所有功能先检查管理范围和功能范围。基础功能使用本地规则、SQLite 和 Pillow；仅糖糖聊天和发言画像会在启用时调用外部模型。",
                ),
            ],
        ),
        (
            "超级管理员手册 2/8｜功能范围与状态",
            "查看和热更新每个功能针对哪些群",
            [
                (
                    "查看总范围",
                    f"{prefix}功能范围 列表\n{prefix}机器人状态\n{prefix}被动互动状态\n\n当前已持久化范围：\n{scope_lines}",
                    "功能范围命令修改后立即生效并写入 SQLite。",
                ),
                (
                    "热更新功能范围",
                    f"{prefix}功能范围 查重 添加 QQ群号\n{prefix}功能范围 游戏 添加 QQ群号\n{prefix}功能范围 游戏接口 添加 QQ群号\n{prefix}功能范围 今日老婆 添加 QQ群号\n{prefix}功能范围 活动 添加 QQ群号\n{prefix}功能范围 被动 添加 QQ群号\n{prefix}功能范围 整点报时 添加 QQ群号",
                    f"移除时把“添加”替换为“移除”。目标群必须属于 MANAGED_GROUP_IDS；未开启的群不会在后台执行对应功能。A海岸发言统计固定为五个指定群，不能通过此命令修改。游戏接口（异环 #nte）范围默认全部管理群，与本地小游戏范围相互独立，用 {prefix}游戏接口 状态|开启|关闭 控制。",
                ),
                (
                    "权限和无效请求",
                    "范围维护只接受超级管理员\n超级管理员可以通过私聊维护全局范围\n群外或未开启的功能按各功能规则处理",
                    "游戏接口命令在未开放群保持无反应；异环 NTE 前缀可带或不带 # 且不区分大小写，gs/ww/yh 一律不响应；统计关闭时统计插件不会加载，也不会读取群消息。",
                ),
            ],
        ),
        (
            "超级管理员手册 3/8｜查重与白名单",
            "跨群成员查重、白名单和图片结果",
            [
                (
                    "四种查重模式",
                    f"{prefix}查重1 群号1 群号2 ...\n{prefix}查重2 起点群 目标群1 目标群2 ...\n{prefix}查重3 群号1 群号2 ...\n{prefix}查重4 起点群 目标群1 目标群2 ...",
                    "查重1：所有指定群互相查重；查重2：只看起点群成员是否在目标群出现；查重3/4分别是不使用白名单的查重1/2。省略群号使用当前配置范围。",
                ),
                (
                    "白名单维护",
                    f"{prefix}白名单 菜单\n{prefix}白名单 列表\n{prefix}白名单 添加 QQ号 [备注]\n{prefix}白名单 删除 QQ号",
                    "白名单只影响普通查重1/2，不影响查重3/4。群内命令需要查重功能已对当前群开放；超级管理员也可在私聊查看、维护白名单。菜单是本地图片，文字命令始终有效。",
                ),
                (
                    "结果和隐私",
                    "查重结果使用本地确定性图片，全部成员合并为一张图\n图片顶部标出 1、2、3 群，成员行只显示重复群编号",
                    "头像和昵称属于普通 QQ 资料请求，不调用 AI。跨群结果只对超级管理员开放；不要把结果转发到无关群。",
                ),
            ],
        ),
        (
            "超级管理员手册 4/8｜被动互动与糖糖主动回复",
            "随机表情、随机复读、三连复读、糖糖主动回复和过滤名单",
            [
                (
                    "被动互动范围",
                    f"{prefix}被动互动群 列表\n{prefix}被动互动群 添加 QQ群号\n{prefix}被动互动群 移除 QQ群号\n{prefix}被动互动状态 [QQ群号]",
                    "只有被动互动范围内的普通非命令纯文本消息会进入被动处理。每个群都有独立参数，热更新立即持久化。",
                ),
                (
                    "随机表情和随机复读",
                    f"{prefix}表情命中率 QQ群号 0-50%\n{prefix}表情冷却 QQ群号 秒\n{prefix}复读命中率 QQ群号 0-10%\n{prefix}复读冷却 QQ群号 分钟\n{prefix}复读间隔 QQ群号 消息条数",
                    "随机表情受命中率和冷却影响；随机复读还要满足纯文本、消息间隔和冷却。全局启动开关仍来自 .env，逐群参数可热更。",
                ),
                (
                    "三连复读",
                    f"{prefix}三连复读 列表\n{prefix}三连复读 QQ群号 开启\n{prefix}三连复读 QQ群号 关闭\n{prefix}三连复读 QQ群号 状态\n{prefix}三连复读 QQ群号 概率 0-100%",
                    "三连复读属于被动互动范围，有独立逐群开关和命中概率。连续三条完全相同的纯文本后按概率额外复读一次；不受随机复读命中率、冷却和消息间隔影响。过滤名单用户、机器人自身消息和 #命令不会触发。",
                ),
                (
                    "糖糖主动回复",
                    f"{prefix}糖糖主动回复 状态\n{prefix}糖糖主动回复 开启\n{prefix}糖糖主动回复 关闭\n{prefix}糖糖主动回复 概率 0-20%\n{prefix}糖糖主动回复 冷却 分钟\n{prefix}糖糖主动回复 间隔 消息条数",
                    "开启后，糖糖会对未呼叫的普通群消息按概率尝试主动接话；参数写回 .env，重启后保持一致。可与复读类被动互动并行，重叠时请调低复读或主动回复概率。",
                ),
                (
                    "主动与被动过滤名单",
                    f"{prefix}主动过滤 QQ号1,QQ号2\n{prefix}移除主动过滤 QQ号1,QQ号2\n{prefix}主动过滤 列表\n\n{prefix}被动过滤 QQ号1,QQ号2\n{prefix}移除被动过滤 QQ号1,QQ号2\n{prefix}被动过滤 列表",
                    "主动名单静默拦截被过滤用户的 # 指令和小游戏；被动名单阻止随机表情/复读、三连复读和 @机器人的自动表情。两份名单独立保存，支持逗号、中文逗号或空格批量填写。",
                ),
            ],
        ),
        (
            "超级管理员手册 5/8｜活动用户操作",
            "普通用户如何查看、报名和查询结果",
            [
                (
                    "查看活动",
                    f"{prefix}活动帮助\n{prefix}活动大厅\n{prefix}活动详情 ID\n{prefix}查看名单 ID\n{prefix}我的活动",
                    "活动大厅只显示未开始和进行中的活动。活动详情、名单和结果都使用本地图片；多图内容会折叠合并转发。ID 从 500 起连续生成。",
                ),
                (
                    "报名和退出",
                    f"{prefix}报名 ID\n{prefix}取消报名 ID\n也支持：报名517、报名 517、报名：517、报名#517",
                    "活动开放群内的所有用户均可不 @ 进行活动操作。报名成功会添加续标识并引用回复当前人数；重复报名只添加续标识；取消报名会添加续标识和疑问表情。活动结束后不能报名或退出。",
                ),
                (
                    "名单隐私",
                    f"{prefix}获奖名单 ID\n{prefix}查看获奖名单 ID",
                    "群聊始终是公开上下文：普通用户、活动管理员和超级管理员看到的跨群名单都按活动设置脱敏。活动创建者和超级管理员可在私聊查看自己有权限活动的完整信息。公开参与活动才在群内显示完整参与信息。",
                ),
            ],
        ),
        (
            "超级管理员手册 6/8｜活动创建与管理",
            "创建、修改、取消、提前结束和自动结算",
            [
                (
                    "创建活动",
                    f"{prefix}创建活动\n活动名：名称\n开始时间：YYYY-M-D HH:MM\n结束时间：YYYY-M-D HH:MM\n类型：通报/抽奖\n参与群：群号1,群号2",
                    "超级管理员、配置的活动管理员，以及活动有效群内的群主和群管理员可创建。参与群必须属于活动功能范围；抽奖必须填写奖项，通报不要填写奖项。",
                ),
                (
                    "格式示例",
                    f"{prefix}创建活动\n活动名：夏日抽奖\n开始时间：2026-7-25 20:00\n结束时间：2026-7-25 22:00\n类型：抽奖\n奖项：一等奖=1；二等奖=3\n隐私：公开",
                    "奖项格式：等号前为奖项内容、等号后为数量；多个奖项使用分号分隔。每个字段独占一行，未填写的可选字段使用默认值。",
                ),
                (
                    "修改活动",
                    f"{prefix}修改活动\n活动ID：517\n活动名：新标题\n结束时间：2026-7-25 22:30",
                    "活动ID 是必填定位字段，活动名、开始时间、结束时间、类型、说明、奖项、参与群、隐私可修改；未填写或留空保持原值。进行中的活动仅超级管理员可修改；参与群变化会通知相关群。",
                ),
                (
                    "结束和取消",
                    f"{prefix}提前结束 ID\n{prefix}取消活动 ID [原因]",
                    "活动创建者只能管理自己创建的活动；超级管理员可以管理全部活动。取消是软删除并保留审计记录；抽奖活动结束时自动开奖，同一用户一场活动最多中奖一次。",
                ),
            ],
        ),
        (
            "超级管理员手册 7/8｜直播、枝江、A-SOUL 与 A海岸",
            "直播日程、B站维护、枝江资料和固定统计范围",
            [
                (
                    "A海岸发言统计",
                    f"{prefix}发言排行 日|周|月|总\n{prefix}A海岸发言排行 日|周|月|总\n{prefix}发言画像 QQ号|@成员\n{prefix}画像 QQ号|@成员",
                    f"A海岸五群内的所有用户均可调用两种排行榜；不需要超级管理员权限。超级管理员私聊可使用 {prefix}A海岸发言排行 日|周|月|总 查看全部 A海岸榜单。灌水榜记录自 2026-07-28 起。\n\n群内榜只计算当前群；A海岸榜合并五群，但每位用户只显示其在当前统计窗口内发言次数最高的一个完整群名；次数并列时按 A海岸固定群顺序判定。日、周、月、总榜均取前 100，图片显示头像和昵称但不显示 QQ 号。\n\n每天 23:50-23:59 会向五个 A海岸群各自动发送一次 A海岸日榜；窗口内重连会补发未成功的群。\n\n发言原文统一保存在糖糖本地聊天记录库，全部固定覆盖五个 A海岸群，不提供单群范围：A海岸群成员可在群内使用 {prefix}发言画像 QQ号；超级管理员私聊也可使用画像。画像以动态长图展示头像、QQ 号、确立时间、24 小时发言占比、本地六维文本风格与 AI 正文。{prefix}发言记录 QQ号 [页码]、{prefix}发言搜索 QQ号 关键词 [页码] 仍仅超级管理员私聊可用。\n\n每条原文只会用于一次 AI 画像，之后以既有画像加新发言更新。统计只保存 QQ 号、最后昵称和发言次数，不保存消息内容；范围固定，不能通过功能范围命令修改。",
                ),
                (
                    "枝江直播守卫与百科",
                    f"{prefix}枝江直播 [状态]\n{prefix}刷新枝江直播",
                    "枝江直播显示未来日程；状态显示守卫和小游戏暂停状态；-a 隐藏心宜、思诺场次。刷新枝江直播仅超级管理员可用。直播守卫会在识别到直播期间自动暂停小游戏；百科为本地资料检索。",
                ),
                (
                    "A-SOUL（A手）日程与 B站",
                    f"{prefix}A魂帮助\n{prefix}今日直播\n{prefix}明日直播\n{prefix}本周直播\n{prefix}日程高亮 YYYY-MM-DD [序号] [粉色|红色|白金色]\n{prefix}取消日程高亮 YYYY-MM-DD 序号\n{prefix}日程高亮列表\n{prefix}取消日程高亮记录 序号",
                    f"A-SOUL 日程对所有人可查；日程高亮仅超级管理员可维护。B站自动播报状态使用 {prefix}bili_status；登录或退出使用 {prefix}bili_login、{prefix}bili_logout，登录请在私聊完成。B站接口测试和原始数据导出只在私聊使用，避免将调试数据发送到群聊。",
                ),
            ],
        ),
        (
            "超级管理员手册 8/8｜游戏、报时与维护",
            "当前开关、维护命令和故障排查",
            [
                (
                    "游戏和整点报时",
                    f"游戏范围：{prefix}功能范围 游戏 添加|移除 QQ群号\n游戏接口：{prefix}游戏接口 状态|开启|关闭，{prefix}功能范围 游戏接口 添加|移除 QQ群号\n小游戏清理：{prefix}清游 → {prefix}确认 / {prefix}取消\n今日老婆清理：{prefix}清缘 → {prefix}确认清缘 / {prefix}取消清缘\n{prefix}整点报时 状态\n{prefix}整点报时 开启|关闭\n{prefix}整点报时 时段 HH:MM HH:MM\n{prefix}整点报时 范围 添加|移除|列表 QQ群号",
                    f"#清游 只清小游戏对局和战绩；#清缘 只清当前群的今日老婆关系历史，近三天匿名活跃计数保留，两个操作各自需要 60 秒内确认。游戏接口识别 NTE 前缀（可带或不带 #，大小写不敏感），默认全部管理群可用，与小游戏开关、直播守卫互不影响。整点报时只在开启状态和目标群发送。",
                ),
                (
                    "A海岸全群通告",
                    f"{prefix}全局通告 [@全体] [人物] [表情包名] <内容>\n{prefix}全局图片公告 [@全体]\n{prefix}图片公告 [@全体]",
                    "仅超级管理员可用。图片公告可回复一张图片后发送指令，也可将指令和图片放在同一条消息中；只取第一张图片，固定发送到五个 A海岸群。默认不 @ 全体，加入 @全体 或 @all 才会提醒全体成员。",
                ),
                (
                    "Codex 持续任务",
                    f"{prefix}Codex <需求>\n{prefix}Codex 续 ID <补充需求>\n{prefix}启动Codex ID\n{prefix}暂停Codex ID\n{prefix}取消Codex ID\n{prefix}Codex 状态|列表|结果|重试 ID",
                    "仅超级管理员可用。新任务与续办都先入队，必须用启动命令执行；同一 ID 会恢复同一 Codex 会话上下文。全机仅串行执行一个任务，完成、失败或停止后均向固定通知群发送折叠结果。详细说明见《Codex远程持续任务操作手册.md》。",
                ),
                (
                    "本机维护",
                    "restart_nonebot.bat\nPowerShell：.\\scripts\\stop.ps1\nPowerShell：.\\scripts\\start.ps1\n配置校验：.\\venv\\Scripts\\python.exe scripts\\validate_qq_config.py --env .env",
                    f"只改 NoneBot 时不用重启 NapCat；检查日志目录 logs，OneBot 地址为 {settings.host}:{settings.port}。不要把 .env 中的 API Key、OneBot Token 或 NapCat Token 发到群里。",
                ),
            ],
        ),
    ]


async def send_super_admin_help(bot: Bot, matcher: object, event: MessageEvent) -> None:
    pages = super_admin_help_pages()
    paths: list[Path | None] = []
    texts: list[str] = []
    messages: list[dict[str, Any]] = []
    for title, subtitle, sections in pages:
        text = admin_help_page_text(title, sections)
        texts.append(text)
        try:
            path: Path | None = report_renderer.render_admin_panel(title, subtitle, sections)
        except Exception:
            logger.exception("Super admin help page rendering failed: %s", title)
            path = None
        paths.append(path)
        page_nodes = build_forward_nodes([f"{title} 图片", text], [path, None], bot.self_id, title="超级管理员帮助")
        messages.extend(page_nodes)

    try:
        group_id = current_group(event)
        if group_id is None:
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
                group_id=group_id,
                messages=messages,
            )
    except Exception:
        logger.exception("Super admin help forward failed")
        for path in paths:
            if path is not None:
                await matcher.send(local_image_segment(path))  # type: ignore[attr-defined]
        await matcher.send("\n\n".join(texts))  # type: ignore[attr-defined]
    await matcher.finish()  # type: ignore[attr-defined]


@admin_help.handle()
async def _(bot: Bot, event: MessageEvent):
    if not is_super_admin(user_id(event)):
        await admin_help.finish("只有超级管理员可以查看超级管理员帮助。")
    await send_super_admin_help(bot, admin_help, event)


def filter_user_ids(raw: str) -> tuple[int, ...] | None:
    values = raw.replace("，", ",").replace(",", " ").split()
    if not values:
        return None
    parsed = [valid_qq_id(value) for value in values]
    return tuple(dict.fromkeys(value for value in parsed if value is not None)) if all(parsed) else None


async def handle_filter_command(
    matcher: object,
    event: MessageEvent,
    args: object,
    *,
    kind: str,
    label: str,
    action: str,
) -> None:
    if not is_operator(event):
        await matcher.finish(f"没有维护{label}的权限。")  # type: ignore[attr-defined]
    raw = "" if action == "list" else text_arg(args)
    is_list = action == "list" or raw.lower() in {"列表", "list"}
    if is_list:
        rows = db.filter_members(kind)
        if not rows:
            await matcher.finish(f"{label}为空。")  # type: ignore[attr-defined]
        await finish_admin_feedback(
            matcher,
            label,
            f"共 {len(rows)} 人",
            [("QQ号", "\n".join(str(row["user_id"]) for row in rows), "名单独立持久化，重启后仍然生效。")],
        )
        return
    targets = filter_user_ids(raw)
    if targets is None:
        usage = f"#{'移除' if action == 'remove' else ''}{label} QQ号1,QQ号2 或 #{label} 列表"
        await matcher.finish(f"用法：{usage}")  # type: ignore[attr-defined]
    if action == "add":
        db.add_filter_members(kind, targets, user_id(event))
        db.audit(user_id(event), f"{kind}_filter_add", current_group(event), detail=",".join(map(str, targets)))
        effect = "该用户的所有 # 指令和小游戏操作将静默无效。" if kind == "active" else "该用户不会触发任何非 # 的自动互动。"
        subtitle = "已加入 " + "、".join(map(str, targets))
    else:
        removed = db.remove_filter_members(kind, targets)
        db.audit(user_id(event), f"{kind}_filter_remove", current_group(event), detail=",".join(map(str, targets)))
        effect = "已移除的用户可再次使用 # 指令和小游戏。" if kind == "active" else "已移除的用户可再次触发被动互动。"
        subtitle = "已移除 " + "、".join(map(str, removed)) if removed else "指定 QQ 均不在名单中"
    await finish_admin_feedback(
        matcher,
        f"{label}已更新",
        subtitle,
        [("效果", effect, "支持英文逗号、中文逗号或空格批量填写；变更立即生效。")],
    )


active_filter = on_command("主动过滤", priority=5, block=True)
remove_active_filter = on_command("移除主动过滤", priority=5, block=True)
active_filter_list = on_command("主动过滤列表", priority=5, block=True)
passive_filter = on_command("被动过滤", priority=5, block=True)
remove_passive_filter = on_command("移除被动过滤", priority=5, block=True)
passive_filter_list = on_command("被动过滤列表", priority=5, block=True)


@active_filter.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await handle_filter_command(active_filter, event, args, kind="active", label="主动过滤名单", action="add")


@remove_active_filter.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await handle_filter_command(remove_active_filter, event, args, kind="active", label="主动过滤名单", action="remove")


@active_filter_list.handle()
async def _(event: MessageEvent):
    await handle_filter_command(active_filter_list, event, "", kind="active", label="主动过滤名单", action="list")


@passive_filter.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await handle_filter_command(passive_filter, event, args, kind="passive", label="被动过滤名单", action="add")


@remove_passive_filter.handle()
async def _(event: MessageEvent, args=CommandArg()):
    await handle_filter_command(remove_passive_filter, event, args, kind="passive", label="被动过滤名单", action="remove")


@passive_filter_list.handle()
async def _(event: MessageEvent):
    await handle_filter_command(passive_filter_list, event, "", kind="passive", label="被动过滤名单", action="list")


whitelist = on_command("白名单", priority=5, block=True)


@whitelist.handle()
async def _(bot: Bot, event: MessageEvent, args=CommandArg()):
    await require_feature_group(whitelist, event, "duplicate", "查重")
    if not is_operator(event):
        await whitelist.finish("没有维护查重白名单的权限。")
    tokens = text_arg(args).split()
    if tokens and tokens[0].lower() in {"菜单", "menu"}:
        await finish_whitelist_menu(bot, whitelist, event)
    if not tokens or tokens[0] in {"列表", "list"}:
        rows = db.whitelist_profiles()
        await finish_whitelist_list(bot, whitelist, event, rows)
    if len(tokens) < 2 or not tokens[1].isdigit():
        await whitelist.finish(f"用法：{settings.command_prefix}白名单 添加|删除|列表 QQ号 [备注]")
    action, target = tokens[0].lower(), int(tokens[1])
    if action in {"添加", "add"}:
        db.add_whitelist(target, user_id(event), " ".join(tokens[2:]))
        duplicate_snapshots.clear()
        db.audit(user_id(event), "whitelist_add", detail=str(target))
        row = db.user_profiles((target,)).get(target, {"nickname": "", "avatar_url": ""})
        row.update({"user_id": target, "note": "已加入查重白名单。"})
        await finish_whitelist_change(bot, whitelist, event, row, "已加入查重白名单。")
    if action in {"删除", "移除", "remove", "delete"}:
        removed = db.remove_whitelist(target)
        duplicate_snapshots.clear()
        db.audit(user_id(event), "whitelist_remove", detail=str(target))
        row = db.user_profiles((target,)).get(target, {"nickname": "", "avatar_url": ""})
        row.update({"user_id": target, "note": "已移除。" if removed else "不在白名单中。"})
        await finish_whitelist_change(
            bot,
            whitelist,
            event,
            row,
            "已移除。" if removed else "不在白名单中。",
        )
    await whitelist.finish(f"用法：{settings.command_prefix}白名单 添加|删除|列表 QQ号 [备注]")


whitelist_menu = on_command("白名单菜单", aliases={"白名单列表"}, priority=5, block=True)


@whitelist_menu.handle()
async def _(bot: Bot, event: MessageEvent):
    await require_feature_group(whitelist_menu, event, "duplicate", "查重")
    if not is_operator(event):
        await whitelist_menu.finish("没有维护查重白名单的权限。")
    await finish_whitelist_menu(bot, whitelist_menu, event)


RANKING_SCOPES = {
    "日": ("day", "日"), "日榜": ("day", "日"), "今日": ("day", "日"),
    "周": ("week", "周"), "周榜": ("week", "周"), "本周": ("week", "周"),
    "月": ("month", "月"), "月榜": ("month", "月"), "本月": ("month", "月"),
    "总": ("total", "总"), "总榜": ("total", "总"), "全部": ("total", "总"),
}
RANKING_TITLES = {
    "day": ("本群今日灌水王", "A海岸今日灌水王"),
    "week": ("本群本周灌水王", "A海岸本周灌水王"),
    "month": ("本群本月灌水王", "A海岸本月灌水王"),
    "total": ("本群传奇灌水王", "A海岸传奇灌水王"),
}
RANKING_SUBTITLE = "前 100 名｜按发言数降序、QQ 号升序｜记录自 2026-07-28 起"


async def finish_message_ranking(
    matcher: object, event: MessageEvent, args: Message, a_coast: bool
) -> None:
    group_id = current_group(event)
    tokens = text_arg(args).split()
    if len(tokens) > 1 or (tokens and tokens[0] not in RANKING_SCOPES):
        command = "A海岸发言排行" if a_coast else "发言排行"
        await matcher.finish(f"用法：{settings.command_prefix}{command} 日|周|月|总")  # type: ignore[attr-defined]
    scope, _ = RANKING_SCOPES[tokens[0]] if tokens else RANKING_SCOPES["日"]
    if group_id is None:
        if not a_coast or not is_super_admin(user_id(event)):
            await matcher.finish("发言排行榜仅在 A海岸群聊内可用。")  # type: ignore[attr-defined]
    elif group_id not in stats_service.enabled_groups():
        await matcher.finish("发言排行榜仅在 A海岸群聊内可用。")  # type: ignore[attr-defined]
    rows = user_report_rows(stats_service.ranking_rows(scope, None if a_coast else group_id))
    title = RANKING_TITLES[scope][1 if a_coast else 0]
    group_totals = stats_service.group_totals(scope) if a_coast else ()
    group_avatars = await group_avatar_paths(group_totals) if group_totals else {}
    daily_totals = stats_service.recent_group_daily_totals(group_id) if group_id is not None and not a_coast else ()
    fallback = f"{stats_service.render_rows(rows, title)}\n记录自 2026-07-28 起"
    avatar_paths = await cached_avatar_paths(rows)
    await finish_with_image_or_text(
        matcher,
        fallback,
        lambda: report_renderer.render_ranking(
            rows,
            title,
            RANKING_SUBTITLE,
            avatar_paths,
            show_group_labels=a_coast,
            group_totals=group_totals,
            group_avatar_paths=group_avatars,
            daily_totals=daily_totals,
        ),
        prefix=MessageSegment.at(user_id(event)) if group_id is not None else None,
    )


group_ranking = on_command(
    "发言排行",
    aliases={"发言榜", "统计"},
    rule=Rule(lambda: settings.stats_realtime_enabled),
    priority=5,
    block=True,
)
group_ranking._tangtang_skip_quote = True


@group_ranking.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await finish_message_ranking(group_ranking, event, args, a_coast=False)


a_coast_ranking = on_command(
    "A海岸发言排行",
    aliases={"A海岸发言榜", "A海岸统计", "a海岸发言排行", "a海岸发言榜", "a海岸统计"},
    rule=Rule(lambda: settings.stats_realtime_enabled),
    priority=5,
    block=True,
)
a_coast_ranking._tangtang_skip_quote = True


@a_coast_ranking.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    await finish_message_ranking(a_coast_ranking, event, args, a_coast=True)


status = on_command("机器人状态", priority=5, block=True)


@status.handle()
async def _(event: MessageEvent):
    if not group_allowed(event) and not is_operator(event):
        await status.finish("当前群未纳入机器人管理范围。")
    rows = [dict(row) for row in db.managed_groups()]
    lines = [f"管理群：{len(rows)}/10"]
    for row in rows:
        group_id = int(row["group_id"])
        row["detail"] = group_scope_detail(group_id)
        row["tag"] = "管理群"
    lines.append(
        "实时消息统计：" + ("开启" if settings.stats_realtime_enabled else "关闭")
    )
    lines.append("功能范围：详见上方群信息卡片")
    lines.append(f"活跃活动：{len(db.activities())} 个")
    if settings.gsuid_enabled:
        lines.append("游戏接口：异环 NTEUID（NTE 前缀可带或不带 #，大小写不敏感，独立 GsUID Core 进程）")
    else:
        lines.append("游戏接口：未启用，请设置 GSUID_ENABLED=true")
    fallback = "\n".join(lines)
    status_group_avatar_paths = await group_avatar_paths(rows)
    await finish_with_image_or_text(
        status,
        fallback,
        lambda: report_renderer.render_status(rows, lines[1:], status_group_avatar_paths),
    )


@register_local_feature("ranking")
async def _run_local_ranking_feature(
    matcher: object,
    bot: Bot,
    event: MessageEvent,
    request: FeatureRequest,
) -> None:
    del bot
    await finish_message_ranking(
        matcher,
        event,
        Message(request.args),
        a_coast=request.a_coast,
    )
