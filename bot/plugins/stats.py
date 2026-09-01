from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import HTTPException, Response, status
from fastapi.responses import HTMLResponse
from nonebot import get_bots, get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent
from zoneinfo import ZoneInfo

from bot.config import A_COAST_GROUP_IDS, settings
from bot.services.daily_ranking import DomainDailyRankingDeliveryService
from bot.services.avatars import AvatarService
from bot.services.community_web import (
    DOMAIN_GROUP_KEY,
    CommunityWebRenderer,
    page_html as community_page_html,
    ranking_payload,
)
from bot.services.gateway import GatewayError, OneBotGateway
from bot.services.reports import ReportRenderer
from bot.services.runtime import database
from bot.services.runtime import group_domains
from bot.services.stats import StatsService


db = database()
service = StatsService(
    db,
    realtime_enabled=settings.stats_realtime_enabled,
    group_provider=group_domains().all_group_ids,
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
daily_ranking_delivery = DomainDailyRankingDeliveryService(
    db,
    service,
    group_domains(),
    avatar_service,
    report_renderer,
    group_avatar_service=group_avatar_service,
    community_renderer=community_renderer,
)
scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))


async def build_community_ranking_payload(
    scope: str,
    group_key: str,
    *,
    domain=None,
) -> dict[str, object]:
    """Build the shared online and scheduled ranking payload."""
    domain = domain or group_domains().domain_for_group(A_COAST_GROUP_IDS[0])
    if domain is None:
        raise ValueError("群域不可用")
    domain_group_ids = group_domains().domain_groups(domain.domain_id)
    if group_key == DOMAIN_GROUP_KEY:
        group_id = None
        ranking_group_ids = domain_group_ids
        normalized_group_key = DOMAIN_GROUP_KEY
    else:
        group_id = group_domains().group_id_from_public_key(domain.domain_id, group_key)
        if group_id is None or (domain.mode == "solo" and group_id not in domain_group_ids):
            raise ValueError("group 不受支持")
        ranking_group_ids = (group_id,)
        normalized_group_key = group_key

    rows = service.ranking_rows_for_groups(scope, ranking_group_ids)
    avatar_paths = await avatar_service.prefetch(rows)
    group_totals = (
        service.group_totals_for_groups(scope, domain_group_ids)
        if group_id is None and domain.mode == "cluster"
        else []
    )
    group_avatar_paths = (
        await group_avatar_service.prefetch(
            [{"user_id": int(row["group_id"])} for row in group_totals]
        )
        if group_totals
        else {}
    )
    daily_totals = service.recent_group_daily_totals(group_id) if group_id is not None else []
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
    label = labels[group_id] if group_id is not None else domain_label
    history_since = (
        group_domains().joined_date(group_id)
        if group_id is not None
        else group_domains().domain_joined_date(domain.domain_id)
    )
    return ranking_payload(
        service,
        scope,
        normalized_group_key,
        rows=rows,
        avatar_paths=avatar_paths,
        group_totals=group_totals,
        group_avatar_paths=group_avatar_paths,
        daily_totals=daily_totals,
        selected_group_id=group_id,
        group_label_override=label,
        group_labels=labels,
        group_options=options,
        history_since=history_since,
    )


NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, noarchive",
}


@driver.server_app.get("/ranking/{token}/", response_class=HTMLResponse)
async def domain_ranking_page(token: str) -> HTMLResponse:
    if group_domains().domain_by_token(token) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return HTMLResponse(community_page_html("ranking"), headers=NO_STORE_HEADERS)


@driver.server_app.get("/ranking/{token}/api")
async def domain_ranking_api(
    token: str,
    response: Response,
    scope: str = "day",
    group: str = DOMAIN_GROUP_KEY,
) -> dict[str, object]:
    domain = group_domains().domain_by_token(token)
    if domain is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    response.headers.update(NO_STORE_HEADERS)
    try:
        return await build_community_ranking_payload(scope, group, domain=domain)
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
            logger.warning("Unable to refresh group name for group=%s", group_id)

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
        id="domain-daily-ranking-delivery",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Daily ranking delivery scheduler started; window=23:50-00:30")


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
