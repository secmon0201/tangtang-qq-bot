from __future__ import annotations

import asyncio
import re
from math import ceil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from fastapi import HTTPException, status as fastapi_status
from fastapi.responses import HTMLResponse
from nonebot import get_driver, logger, on_command
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
    current_group,
    is_operator,
    text_arg,
    user_id,
    valid_qq_id,
)
from bot.config import A_COAST_GROUP_IDS, settings
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
from bot.services.operator_web import (
    operator_web_base_url,
    operator_web_sessions,
    operator_web_url,
)
from bot.services.community_web import (
    DOMAIN_GROUP_KEY,
    CommunityWebRenderer,
    help_payload,
    page_html as community_page_html,
    public_help_categories,
    public_domain_ranking_url,
    public_web_url,
    ranking_payload,
)
from bot.services.qq_platform import call_qq_action
from bot.services.roles import is_super_admin
from bot.services.runtime import database, group_domains, passive_settings
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
    group_provider=group_domains().all_group_ids,
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
community_web_renderer = CommunityWebRenderer(settings.report_dir)
driver = get_driver()


@driver.on_startup
async def _warm_community_web_renderer() -> None:
    try:
        await community_web_renderer.warmup()
    except Exception:
        logger.exception("Community HTML renderer warmup failed; Pillow fallback remains available")


@driver.on_shutdown
async def _close_community_web_renderer() -> None:
    await community_web_renderer.close()


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
    return public_help_categories(
        settings.command_prefix, stats_enabled=settings.stats_realtime_enabled
    )


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
            f"{prefix}集群发言排行 [日/周/月/总]", f"{prefix}集群发言榜 [日/周/月/总]", f"{prefix}集群统计 [日/周/月/总]",
            f"{prefix}发言记录 <QQ号|@成员> [页码]", f"{prefix}发言搜索 <QQ号|@成员> <关键词> [页码]",
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
    """Return the interactive help entry instead of a long static image."""
    link = public_web_url("help")
    if link is None:
        await matcher.finish("帮助在线页暂时不可用，请稍后再试。")
        return
    await matcher.finish(f"帮助在线：{link}")


user_help = on_command("帮助", priority=5, block=True)


@user_help.handle()
async def _():
    await send_user_help_image(user_help)


@driver.server_app.get("/help/", response_class=HTMLResponse)
async def community_help_page() -> HTMLResponse:
    return HTMLResponse(community_page_html("help"))


@driver.server_app.get("/help/api")
async def community_help_api() -> dict[str, Any]:
    return help_payload()


@driver.server_app.api_route(
    "/community", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
)
@driver.server_app.api_route(
    "/community/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
)
async def retired_community_route(path: str = "") -> None:
    del path
    raise HTTPException(status_code=fastapi_status.HTTP_404_NOT_FOUND)


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


async def build_community_ranking_payload(
    scope: str,
    group_key: str,
    *,
    rows: list[dict[str, Any]] | None = None,
    domain=None,
    selected_group_id: int | None = None,
) -> dict[str, Any]:
    """Build one current-group or current-cluster ranking card."""
    if scope not in {value[0] for value in RANKING_SCOPES.values()}:
        raise ValueError("scope 仅支持 day、week、month、total")
    domain = domain or group_domains().domain_for_group(int(selected_group_id or 0))
    if domain is None:
        domain = group_domains().domain_for_group(A_COAST_GROUP_IDS[0])
    if domain is None:
        raise ValueError("群域不可用")
    domain_group_ids = group_domains().domain_groups(domain.domain_id)
    group_id = int(selected_group_id) if selected_group_id is not None else None
    ranking_group_ids = (group_id,) if group_id is not None else domain_group_ids

    ranking_rows = (
        rows
        if rows is not None
        else user_report_rows(stats_service.ranking_rows_for_groups(scope, ranking_group_ids))
    )
    avatar_paths = await cached_avatar_paths(ranking_rows)
    group_totals = (
        stats_service.group_totals_for_groups(scope, domain_group_ids)
        if group_id is None and domain.mode == "cluster"
        else []
    )
    group_avatars = await group_avatar_paths(group_totals) if group_totals else {}
    daily_totals = (
        stats_service.recent_group_daily_totals(group_id) if group_id is not None else []
    )
    labels = {
        member_group_id: group_domains().display_name(member_group_id)
        for member_group_id in domain_group_ids
    }
    domain_label = group_domains().domain_display_name(domain)
    options = [{"key": DOMAIN_GROUP_KEY, "label": domain_label}]
    if domain.mode == "cluster":
        options.extend(
            {
                "key": group_domains().public_group_key(member_group_id) or "",
                "label": labels[member_group_id],
            }
            for member_group_id in domain_group_ids
        )
    return ranking_payload(
        stats_service,
        scope,
        DOMAIN_GROUP_KEY if group_id is None else group_key,
        rows=ranking_rows,
        avatar_paths=avatar_paths,
        group_totals=group_totals,
        group_avatar_paths=group_avatars,
        daily_totals=daily_totals,
        selected_group_id=group_id,
        group_label_override=(
            labels[group_id] if group_id is not None else domain_label
        ),
        group_labels=labels,
        group_options=options,
        history_since=(
            group_domains().joined_date(group_id)
            if group_id is not None
            else group_domains().domain_joined_date(domain.domain_id)
        ),
    )


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


duplicate_web = on_command("查重网页", aliases={"查重面板", "白名单网页"}, priority=5, block=True)


async def _open_operator_web(matcher: object, event: MessageEvent, kind: str, label: str) -> None:
    if not is_super_admin(user_id(event)):
        await matcher.finish(f"只有超级管理员可以使用{label}。")  # type: ignore[attr-defined]
    base_url = operator_web_base_url()
    if base_url is None:
        await matcher.finish("查重网页隧道尚未启动，请先启动查重网页隧道。")  # type: ignore[attr-defined]
    session = operator_web_sessions.create(user_id(event), kind, is_super_admin=True)
    await matcher.finish(
        f"{label}（链接仅限本次操作，15 分钟内有效）：\n"
        + operator_web_url(base_url, kind, session.token)
    )  # type: ignore[attr-defined]


@duplicate_web.handle()
async def _(event: MessageEvent):
    await _open_operator_web(duplicate_web, event, "duplicate", "查重与白名单页")


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


def group_scope_detail(group_id: int) -> str:
    return "｜".join(
        f"{row['label']}：{'开' if row['effective_enabled'] else '关'}"
        for row in group_domains().feature_rows(group_id)
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
    active_groups = group_domains().all_group_ids()
    return [
        (
            "超级管理员手册 1/4｜权限与本群设置",
            "群主、群管理员与超级管理员的职责边界",
            [
                (
                    "本群机器人管理员",
                    f"{prefix}群设置\n{prefix}群设置 代称 <名称>\n{prefix}群设置 <功能> 开|关",
                    "群主和 QQ 群管理员自动拥有本群机器人管理权限；超级管理员也可管理当前群。群设置只影响本群，不改变集群统计成员关系。",
                ),
                (
                    "功能开关",
                    f"{prefix}开关小游戏\n{prefix}开关今日老婆\n{prefix}开关准时报点\n{prefix}开关被呼叫会话\n{prefix}开关B站推送\n{prefix}开关被动互动\n{prefix}开关NTE",
                    "新独群默认开启被动指令能力，主动推送默认关闭；加入集群时全部功能开启，之后仍由本群管理员分别开关。",
                ),
                (
                    "本群过滤",
                    f"{prefix}群设置 过滤 列表\n{prefix}群设置 过滤 添加 QQ号\n{prefix}群设置 过滤 移除 QQ号",
                    "过滤名单只作用于当前群，不扩散到同一集群的其他群。",
                ),
            ],
        ),
        (
            "超级管理员手册 2/4｜系统与集群",
            "只在私聊开放的全局管理能力",
            [
                (
                    "集群维护",
                    f"{prefix}系统设置 集群 列表\n{prefix}系统设置 集群 创建 <名称>\n{prefix}系统设置 集群 邀请 <集群ID> <群号>\n{prefix}系统设置 集群 移除 <群号>\n{prefix}系统设置 集群 解散 <集群ID>",
                    "集群不对群管理员开放。成员变化会立即轮换排行令牌；只能邀请机器人当前实际加入的群。",
                ),
                (
                    "全局运行条件",
                    f"{prefix}系统设置 NTE 状态|开|关\n{prefix}系统设置 小游戏 全局 状态|开|关\n{prefix}系统设置 被呼叫会话 状态|开|关\n{prefix}系统设置 糖糖主动聊天 状态|开|关\n{prefix}系统设置 准时报点 状态|开|关|时段 HH:MM HH:MM",
                    "全局运行条件不会改写各群已保存的开关意图；重新开启后，各群按原状态恢复。",
                ),
                (
                    "当前规模",
                    f"已登记群：{len(active_groups)} 个\n群数量不设上限\nSQLite 为运行权威",
                    "新群在机器人首次观察到群消息或同步群列表时自动登记为独群；.env 只用于首次迁移种子和机器级参数。",
                ),
            ],
        ),
        (
            "超级管理员手册 3/4｜统计与 NTE",
            "当前群、当前集群与机器人总榜的明确边界",
            [
                (
                    "发言榜",
                    f"{prefix}发言排行 日|周|月|总\n{prefix}集群发言排行 日|周|月|总",
                    "普通发言榜只统计当前群；集群发言榜只在集群成员群内可用。23:50 推送由各群开关控制：独群发送本群榜，集群成员群发送当前集群榜。",
                ),
                (
                    "发言档案",
                    f"{prefix}发言记录 QQ号|@成员 [页码]\n{prefix}发言搜索 QQ号|@成员 关键词 [页码]\n{prefix}发言画像 QQ号|@成员",
                    "记录和搜索只读取当前群；画像按当前群域读取。历史说明使用糖糖实际加入该群的日期。",
                ),
                (
                    "NTE 排行",
                    f"{prefix}nte薄荷排行\n{prefix}nte薄荷总排行\n{prefix}nte最强排行\n{prefix}nte最强总排行",
                    "默认排行只看当前群；只有显式写出“总排行”才查看机器人记录到的全部群。关闭本群 NTE 只阻止本群主动调用，不改写上游数据。",
                ),
            ],
        ),
        (
            "超级管理员手册 4/4｜公告与运维",
            "多选目标公告、查重与本地运行边界",
            [
                (
                    "公告面板",
                    f"{prefix}公告面板\n兼容：{prefix}公告网页",
                    "仅超级管理员可打开。独群、集群和集群成员可同时多选，实际群号在会话创建时冻结并自动去重；文字、图文和原图使用同一目标规则。",
                ),
                (
                    "查重与白名单",
                    f"{prefix}查重1 / {prefix}查重2 / {prefix}查重3 / {prefix}查重4\n{prefix}白名单 菜单|列表|添加|删除",
                    "查重是独立的内部能力，不随公开群功能列表展示；跨群结果只对超级管理员开放。",
                ),
                (
                    "运行边界",
                    "Windows 本地｜NoneBot2｜OneBot v11｜NapCat｜SQLite\nNTE 上游只读",
                    "QQ 登录与验证始终由用户手工完成。普通 Python 改动只重启 NoneBot，不重启 NapCat/QQ，也不修改 GsUID.Core。",
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
    "day": "今日发言榜",
    "week": "本周发言榜",
    "month": "本月发言榜",
    "total": "累计发言榜",
}


async def finish_message_ranking(
    matcher: object, event: MessageEvent, args: Message, a_coast: bool
) -> None:
    group_id = current_group(event)
    tokens = text_arg(args).split()
    if len(tokens) > 1 or (tokens and tokens[0] not in RANKING_SCOPES):
        command = "集群发言排行" if a_coast else "发言排行"
        await matcher.finish(f"用法：{settings.command_prefix}{command} 日|周|月|总")  # type: ignore[attr-defined]
    scope, _ = RANKING_SCOPES[tokens[0]] if tokens else RANKING_SCOPES["日"]
    if group_id is None:
        await matcher.finish("发言榜需要在目标 QQ 群内查询。")  # type: ignore[attr-defined]
    domain = group_domains().domain_for_group(group_id)
    if domain is None:
        await matcher.finish("当前群尚未完成登记，请稍后重试。")  # type: ignore[attr-defined]
    if a_coast and domain.mode != "cluster":
        await matcher.finish("当前群不属于集群；请使用 #发言榜 查看本群排行。")  # type: ignore[attr-defined]
    ranking_group_ids = (
        group_domains().domain_groups(domain.domain_id) if a_coast else (group_id,)
    )
    ranking_rows = stats_service.ranking_rows_for_groups(scope, ranking_group_ids)
    rows = user_report_rows(ranking_rows)
    public_group_key = group_domains().public_group_key(group_id) or DOMAIN_GROUP_KEY
    web_payload = await build_community_ranking_payload(
        scope,
        DOMAIN_GROUP_KEY if a_coast else public_group_key,
        rows=rows,
        domain=domain,
        selected_group_id=None if a_coast else group_id,
    )
    title = str(web_payload["title"])
    message_prefix = Message(MessageSegment.at(user_id(event))) if group_id is not None else Message()
    link = public_domain_ranking_url(domain.public_token)
    try:
        image = await community_web_renderer.render_ranking(web_payload)
    except Exception:
        logger.exception("Community ranking HTML render failed; trying Pillow fallback")
        fallback = f"{stats_service.render_rows(rows, title)}\n{web_payload['subtitle']}"
        avatar_paths = await cached_avatar_paths(rows)
        group_totals = (
            stats_service.group_totals_for_groups(scope, ranking_group_ids)
            if a_coast
            else ()
        )
        group_avatars = await group_avatar_paths(group_totals) if group_totals else {}
        daily_totals = (
            stats_service.recent_group_daily_totals(group_id)
            if group_id is not None and not a_coast
            else ()
        )
        try:
            image = report_renderer.render_ranking(
                rows,
                title,
                str(web_payload["subtitle"]),
                avatar_paths,
                show_group_labels=a_coast,
                group_totals=group_totals,
                group_avatar_paths=group_avatars,
                daily_totals=daily_totals,
            )
            payload = message_prefix + local_image_segment(image)
        except Exception:
            logger.exception("Pillow ranking fallback failed; using text")
            payload = message_prefix + fallback
    else:
        payload = message_prefix + local_image_segment(image)
    if link is not None:
        payload += f"\n在线：{link}"
    await matcher.finish(payload)


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
    "集群发言排行",
    aliases={"集群发言榜", "集群统计", "A海岸发言排行", "A海岸发言榜", "A海岸统计", "a海岸发言排行", "a海岸发言榜", "a海岸统计"},
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
    lines = [f"管理群：{len(rows)}（数量不设上限）"]
    for row in rows:
        group_id = int(row["group_id"])
        row["detail"] = group_scope_detail(group_id)
        row["tag"] = "管理群"
    lines.append(
        "实时消息统计：" + ("开启" if settings.stats_realtime_enabled else "关闭")
    )
    lines.append("功能范围：详见上方群信息卡片")
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
