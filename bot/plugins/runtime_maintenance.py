from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_driver, logger

from bot.config import settings
from bot.services.runtime_retention import (
    cleanup_avatar_versions,
    cleanup_gsuid_logs,
    cleanup_screenshot_outputs,
)


CLEANUP_INTERVAL_HOURS = 6

driver = get_driver()
scheduler = AsyncIOScheduler(timezone=settings.timezone)


def _cleanup() -> None:
    avatars = cleanup_avatar_versions(
        (settings.avatar_cache_dir, settings.avatar_cache_dir / "groups")
    )
    screenshots = cleanup_screenshot_outputs(
        settings.report_dir,
        retention_hours=settings.report_retention_hours,
    )
    logs = cleanup_gsuid_logs(settings.gsuid_core_dir / "data" / "logs")
    removed_mib = (
        avatars.removed_bytes + screenshots.removed_bytes + logs.removed_bytes
    ) / 1024 / 1024
    logger.info(
        f"Runtime file cleanup completed; avatars={avatars.removed_files}/{avatars.scanned_files} "
        f"screenshots={screenshots.removed_files}/{screenshots.scanned_files} "
        f"gsuid_logs={logs.removed_files}/{logs.scanned_files} removed_mib={removed_mib:.2f}"
    )


@driver.on_startup
async def _start_runtime_maintenance() -> None:
    scheduler.add_job(
        _cleanup,
        "interval",
        hours=CLEANUP_INTERVAL_HOURS,
        id="runtime-file-cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(ZoneInfo(settings.timezone)),
    )
    scheduler.start()
    logger.info(f"Runtime file cleanup scheduler started; interval={CLEANUP_INTERVAL_HOURS}h")


@driver.on_shutdown
async def _stop_runtime_maintenance() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
