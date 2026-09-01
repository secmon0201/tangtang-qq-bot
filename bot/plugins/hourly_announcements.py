from __future__ import annotations

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger
from nonebot.adapters.onebot.v11 import Bot

from bot.config import settings
from bot.services.hourly_announcements import HourlyAnnouncementService
from bot.services.runtime import database, passive_settings

db = database()
passive = passive_settings()
service = HourlyAnnouncementService(db, lambda: passive.groups("hourly"))
driver = get_driver()
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


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
