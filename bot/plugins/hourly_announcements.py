from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg

from bot.config import settings
from bot.services.hourly_announcements import HourlyAnnouncementService
from bot.services.runtime import database, passive_settings

from bot.application.command_helpers import (
    current_group,
    is_operator,
    text_arg,
    user_id,
    valid_qq_id,
)
from bot.application.admin_ui import finish_admin_feedback, finish_group_overview, group_card_rows


db = database()
passive = passive_settings()
service = HourlyAnnouncementService(db, lambda: passive.groups("hourly"))
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


def parse_clock(value: str) -> int | None:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


async def finish_hourly_overview(
    matcher: Any,
    title: str,
    subtitle: str,
    group_ids: Iterable[int] | None = None,
) -> None:
    ids = tuple(sorted({int(group_id) for group_id in (group_ids or passive.groups("hourly"))}))
    if not ids:
        await finish_admin_feedback(
            matcher,
            title,
            subtitle,
            [
                (
                    "整点报时",
                    "#整点报时 开启|关闭\n"
                    "#整点报时 时段 09:00 23:00\n"
                    "#功能范围 整点报时 添加 QQ群号",
                    service.schedule_text() + "。当前没有加入整点报时的群。",
                )
            ],
        )
        return
    rows = await group_card_rows(
        ids,
        {group_id: service.group_detail(group_id) for group_id in ids},
        {group_id: "整点报时" for group_id in ids},
    )
    await finish_group_overview(matcher, title, subtitle + "；" + service.schedule_text(), rows)


async def hourly_maintenance(bot: Bot | None = None) -> None:
    target = bot
    if target is None:
        bots = get_bots()
        target = next(iter(bots.values()), None) if bots else None
    await service.deliver_once(target)


@driver.on_startup
async def _start_hourly_scheduler() -> None:
    scheduler.add_job(
        hourly_maintenance,
        "interval",
        seconds=30,
        id="hourly-announcement-maintenance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Hourly announcement scheduler started; interval=30s, timezone=%s",
        settings.timezone,
    )


@driver.on_shutdown
async def _stop_hourly_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


hourly = on_command("整点报时", priority=5, block=True)


@hourly.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not is_operator(event):
        await hourly.finish("没有维护整点报时的权限。")
    tokens = text_arg(args).split()
    if not tokens or tokens[0].lower() in {"状态", "status"}:
        await finish_hourly_overview(hourly, "整点报时状态", "当前持久化配置")
    action = tokens[0].lower()
    if action in {"开启", "打开", "on"} and len(tokens) == 1:
        service.set_enabled(True)
        db.audit(user_id(event), "hourly_announcement_enable", current_group(event), "true")
        await finish_hourly_overview(hourly, "整点报时已开启", "配置已热更新")
    if action in {"关闭", "停用", "off"} and len(tokens) == 1:
        service.set_enabled(False)
        db.audit(user_id(event), "hourly_announcement_enable", current_group(event), "false")
        await finish_hourly_overview(hourly, "整点报时已关闭", "配置已热更新")
    if action in {"时段", "时间", "schedule"}:
        if len(tokens) != 3:
            await hourly.finish("用法：#整点报时 时段 开始时间 结束时间，例如 09:00 23:00")
        start = parse_clock(tokens[1])
        end = parse_clock(tokens[2])
        if start is None or end is None:
            await hourly.finish("时间格式必须是 HH:MM，例如 09:00。")
        service.set_schedule(start, end)
        db.audit(
            user_id(event),
            "hourly_announcement_schedule",
            current_group(event),
            f"start={tokens[1]};end={tokens[2]}",
        )
        await finish_hourly_overview(hourly, "整点报时时段已更新", "配置已热更新")
    if action in {"范围", "群", "groups"}:
        if len(tokens) == 1 or tokens[1].lower() in {"列表", "list"}:
            await finish_hourly_overview(hourly, "整点报时群范围", "当前已持久化范围")
        if len(tokens) != 3:
            await hourly.finish("用法：#整点报时 范围 添加|移除|列表 QQ群号")
        scope_action = tokens[1].lower()
        group_id = valid_qq_id(tokens[2])
        if group_id is None:
            await hourly.finish("QQ群号必须是数字。")
        if scope_action in {"添加", "add"}:
            try:
                groups = passive.add_feature_group("hourly", group_id)
            except ValueError:
                await hourly.finish("该群未在机器人管理范围内，不能加入整点报时范围。")
            db.audit(user_id(event), "hourly_announcement_group_add", current_group(event), str(group_id))
            await finish_hourly_overview(hourly, "整点报时范围已更新", f"已加入群 {group_id}", groups)
        if scope_action in {"移除", "删除", "remove", "delete"}:
            groups = passive.remove_feature_group("hourly", group_id)
            db.audit(user_id(event), "hourly_announcement_group_remove", current_group(event), str(group_id))
            await finish_hourly_overview(hourly, "整点报时范围已更新", f"已移除群 {group_id}", groups)
    await hourly.finish(
        "用法：#整点报时 状态|开启|关闭|时段 HH:MM HH:MM|范围 添加|移除|列表 QQ群号"
    )
