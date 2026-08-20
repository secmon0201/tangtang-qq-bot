from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.services.a_coast_daily_ranking import ACoastDailyRankingDeliveryService
from bot.services.avatars import AvatarService
from bot.services.gateway import GatewayError, OneBotGateway
from bot.services.reports import ReportRenderer
from bot.services.runtime import database
from bot.services.stats import StatsService


db = database()
service = StatsService(
    db,
    realtime_enabled=settings.stats_realtime_enabled,
)
driver = get_driver()
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
report_renderer = ReportRenderer(
    settings.report_dir,
    settings.report_font_path,
    settings.report_retention_hours,
    settings.timezone,
)
daily_ranking_delivery = ACoastDailyRankingDeliveryService(
    db,
    service,
    avatar_service,
    report_renderer,
    group_avatar_service=group_avatar_service,
)
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


@driver.on_bot_connect
async def _(bot: Bot):
    gateway = OneBotGateway(bot)
    for group_id in service.enabled_groups():
        try:
            name = str((await gateway.group_info(group_id)).get("group_name") or "").strip()
            if name:
                db.set_group_info(group_id, name[:80])
        except GatewayError:
            logger.warning("Unable to refresh A海岸 group name for group=%s", group_id)

    await daily_ranking_delivery.deliver_once(bot)


async def scheduled_daily_ranking_delivery() -> None:
    target = next(iter(get_bots().values()), None)
    await daily_ranking_delivery.deliver_once(target)


@driver.on_startup
async def _start_daily_ranking_scheduler() -> None:
    scheduler.add_job(
        scheduled_daily_ranking_delivery,
        "interval",
        seconds=30,
        id="a-coast-daily-ranking-delivery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("A海岸 daily ranking delivery scheduler started; window=23:50-23:59")


@driver.on_shutdown
async def _stop_daily_ranking_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


if settings.stats_realtime_enabled:
    async def stats_group_rule(event: MessageEvent) -> bool:
        return isinstance(event, GroupMessageEvent) and int(event.group_id) in service.enabled_groups()

    # Count before passive listeners such as random reactions, which intentionally
    # block later matchers after deciding whether to react.
    message_listener = on_message(rule=stats_group_rule, priority=-100, block=False)

    @message_listener.handle()
    async def _(bot: Bot, event: MessageEvent):
        if not isinstance(event, GroupMessageEvent):
            return
        await service.on_message(bot, event)
