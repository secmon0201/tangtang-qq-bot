from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from zoneinfo import ZoneInfo

from bot.config import settings
from bot.services.a_coast_daily_ranking import ACoastDailyRankingDeliveryService
from bot.services.avatars import AvatarService
from bot.services.community_web import (
    ALL_GROUP_KEY,
    CommunityWebRenderer,
    page_html as community_page_html,
    ranking_payload,
)
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
community_renderer = CommunityWebRenderer(settings.report_dir)
daily_ranking_delivery = ACoastDailyRankingDeliveryService(
    db,
    service,
    avatar_service,
    report_renderer,
    group_avatar_service=group_avatar_service,
    community_renderer=community_renderer,
)
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


async def build_community_ranking_payload(
    scope: str,
    group_key: str,
) -> dict[str, object]:
    """Build the shared online and scheduled ranking payload."""
    if group_key == ALL_GROUP_KEY:
        group_id = None
    else:
        try:
            group_id = int(group_key)
        except ValueError as exc:
            raise ValueError("group 不受支持") from exc
        if group_id not in service.enabled_groups():
            raise ValueError("group 不受支持")

    rows = service.ranking_rows(scope, group_id)
    avatar_paths = await avatar_service.prefetch(rows)
    group_totals = service.group_totals(scope) if group_id is None else []
    group_avatar_paths = (
        await group_avatar_service.prefetch(
            [{"user_id": int(row["group_id"])} for row in group_totals]
        )
        if group_totals
        else {}
    )
    daily_totals = service.recent_group_daily_totals(group_id) if group_id is not None else []
    return ranking_payload(
        service,
        scope,
        group_key,
        rows=rows,
        avatar_paths=avatar_paths,
        group_totals=group_totals,
        group_avatar_paths=group_avatar_paths,
        daily_totals=daily_totals,
    )


@driver.server_app.get("/community/ranking/", response_class=HTMLResponse)
async def community_ranking_page() -> HTMLResponse:
    return HTMLResponse(community_page_html("ranking"))


@driver.server_app.get("/community/ranking/api")
async def community_ranking_api(
    scope: str = "day", group: str = ALL_GROUP_KEY
) -> dict[str, object]:
    try:
        return await build_community_ranking_payload(scope, group)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


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
    await community_renderer.close()


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
