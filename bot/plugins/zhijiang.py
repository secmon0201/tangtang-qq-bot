from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from zoneinfo import ZoneInfo

from bot.application.local_features import FeatureRequest, register_local_feature
from bot.config import settings
from bot.services.asoul import ASoulService
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.roles import is_super_admin
from bot.services.runtime import database, zhijiang_live_guard
from bot.services.zhijiang_live_reports import ZhijiangLiveReportRenderer
from bot.services.media import local_image_segment


guard = zhijiang_live_guard()
asoul_schedule = ASoulService(database())
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))
live_report_renderer = ZhijiangLiveReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
)
asoul_renderer = ASoulImageRenderer(settings.report_dir, settings.report_font_path)


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


async def _refresh_schedule() -> None:
    started = await guard.refresh_and_apply(_now())
    if started:
        logger.warning(
            "Zhijiang live guard disabled global mini-games for: {}",
            ", ".join(f"{item.category} {item.starts_at:%m-%d %H:%M}" for item in started),
        )


async def _tick_schedule() -> None:
    started = await guard.tick_async(_now())
    if started:
        logger.warning(
            "Zhijiang live guard disabled global mini-games from cached schedule for: {}",
            ", ".join(f"{item.category} {item.starts_at:%m-%d %H:%M}" for item in started),
        )


async def _finish_with_live_image(matcher: object, fallback: str, render) -> None:
    """Send a local schedule card, retaining text as an operational fallback."""
    if settings.report_output_mode != "local_image":
        await matcher.finish(fallback)  # type: ignore[attr-defined]
    try:
        path = render()
        await matcher.send(local_image_segment(path))  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Zhijiang live image reply failed; using text output")
        await matcher.finish(fallback)  # type: ignore[attr-defined]
    await matcher.finish()  # type: ignore[attr-defined]


async def _finish_with_asoul_week_schedule(matcher: object, omit_filtered_hosts: bool = False) -> None:
    """Share the exact public A-SOUL calendar used by #本周直播."""
    now = _now()
    first = now.date()
    last = first + timedelta(days=6 - first.weekday())
    schedules = await asoul_schedule.schedule_for_days(first, last)
    day_items = []
    for offset in range((last - first).days + 1):
        target_day = first + timedelta(days=offset)
        items = schedules.get(target_day, [])
        if omit_filtered_hosts:
            items = [item for item in items if not set(item.hosts).intersection({"心宜", "思诺"})]
        day_items.append((target_day, items))
    fallback = "\n\n".join(
        asoul_schedule.render_schedule(target_day, "本周直播", items)
        for target_day, items in day_items
    )
    if settings.report_output_mode != "local_image":
        await matcher.finish(fallback)  # type: ignore[attr-defined]
    try:
        path = await asoul_renderer.render_week_schedule(day_items)
        await matcher.send(local_image_segment(path))  # type: ignore[attr-defined]
    except Exception:
        logger.exception("Shared A-SOUL weekly schedule image reply failed; using text output")
        await matcher.finish(fallback)  # type: ignore[attr-defined]
    await matcher.finish()  # type: ignore[attr-defined]


@driver.on_startup
async def _start_zhijiang_live_guard() -> None:
    if not guard.enabled:
        logger.info("Zhijiang live guard is disabled")
        return
    await _refresh_schedule()
    scheduler.add_job(
        _refresh_schedule,
        "interval",
        minutes=settings.zhijiang_schedule_refresh_minutes,
        id="zhijiang-live-refresh",
        replace_existing=True,
    )
    scheduler.add_job(
        _tick_schedule,
        "interval",
        seconds=15,
        id="zhijiang-live-tick",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Zhijiang live guard started; refresh={}m pause={}m source={}",
        settings.zhijiang_schedule_refresh_minutes,
        settings.zhijiang_live_pause_minutes,
        settings.zhijiang_schedule_url,
    )


@driver.on_shutdown
async def _stop_zhijiang_live_guard() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


live_schedule = on_command("枝江直播", aliases={"直播日程"}, priority=5, block=True)


async def finish_zhijiang_schedule(matcher: object, argument: str = "") -> None:
    argument = argument.strip()
    if argument in {"状态", "status"}:
        status = guard.status(_now())
        lines = [
            f"直播防护：{'开启' if status.enabled else '关闭'}",
            f"小游戏总开关：{'开启' if status.global_game_enabled else '关闭'}",
            f"自动暂停至：{status.paused_until:%Y-%m-%d %H:%M}" if status.paused_until else "自动暂停：无",
            f"最近刷新：{status.last_refresh or '尚未成功'}",
        ]
        if status.last_error:
            lines.append(f"最近错误：{status.last_error}")
        await _finish_with_live_image(
            matcher,
            "\n".join(lines),
            lambda: live_report_renderer.render_status(status, _now()),
        )
        return

    await _finish_with_asoul_week_schedule(matcher, argument == "-a")


@live_schedule.handle()
async def _(args=CommandArg()):
    await finish_zhijiang_schedule(live_schedule, args.extract_plain_text())


refresh_live_schedule = on_command("刷新枝江直播", priority=5, block=True)


@refresh_live_schedule.handle()
async def _(event: MessageEvent):
    if not is_super_admin(int(event.user_id)):
        message = "只有超级管理员可以手动刷新直播日程。"
        await _finish_with_live_image(
            refresh_live_schedule,
            message,
            lambda: live_report_renderer.render_notice("无法刷新枝江直播", message, level="warning"),
        )
    if not guard.enabled:
        message = "枝江直播防护未开启。"
        await _finish_with_live_image(
            refresh_live_schedule,
            message,
            lambda: live_report_renderer.render_notice("无法刷新枝江直播", message, level="warning"),
        )
    await _refresh_schedule()
    status = guard.status(_now())
    if status.last_error:
        fallback = f"刷新失败，已保留本地缓存：{status.last_error}"
    else:
        fallback = (
            f"直播日程已刷新，未来 7 天可识别直播 {len(status.upcoming)} 条；"
            f"小游戏总开关当前{'开启' if status.global_game_enabled else '关闭'}。"
        )
    await _finish_with_live_image(
        refresh_live_schedule,
        fallback,
        lambda: live_report_renderer.render_refresh(status, _now()),
    )


@register_local_feature("zhijiang_schedule")
async def _run_local_zhijiang_feature(
    matcher: Any,
    bot: Bot,
    event: Any,
    request: FeatureRequest,
) -> None:
    del bot, event
    await finish_zhijiang_schedule(matcher, request.args)


@register_local_feature("zhijiang_status")
async def _run_local_zhijiang_status(
    matcher: object,
    bot: Bot,
    event: GroupMessageEvent,
    request: FeatureRequest,
) -> None:
    del bot, event, request
    await finish_zhijiang_schedule(matcher, "状态")
