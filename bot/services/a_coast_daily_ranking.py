from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11.exception import ActionFailed

from bot.config import A_COAST_GROUP_IDS, settings
from bot.db import Database
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action


TITLE = "A海岸今日灌水王"
SUBTITLE = "前 100 名｜按发言数降序、QQ 号升序｜记录自 2026-07-28 起"
WINDOW_START = time(23, 50)
WINDOW_END = time(23, 59, 59, 999999)


class ACoastDailyRankingDeliveryService:
    """Persist and deliver one A海岸 daily-ranking poster to every fixed group."""

    def __init__(
        self,
        database: Database,
        stats_service: Any,
        avatar_service: Any,
        report_renderer: Any,
        group_ids: Iterable[int] = A_COAST_GROUP_IDS,
        group_avatar_service: Any | None = None,
    ) -> None:
        self.database = database
        self.stats_service = stats_service
        self.avatar_service = avatar_service
        self.group_avatar_service = group_avatar_service
        self.report_renderer = report_renderer
        self.group_ids = tuple(dict.fromkeys(int(group_id) for group_id in group_ids))
        self.zone = ZoneInfo(settings.timezone)
        self._lock = asyncio.Lock()

    def local_now(self, now: datetime | None = None) -> datetime:
        if now is None:
            return datetime.now(self.zone)
        if now.tzinfo is None:
            return now.replace(tzinfo=self.zone)
        return now.astimezone(self.zone)

    @staticmethod
    def in_delivery_window(now: datetime) -> bool:
        return WINDOW_START <= now.timetz().replace(tzinfo=None) <= WINDOW_END

    async def deliver_once(
        self, bot: Any | None = None, now: datetime | None = None
    ) -> dict[str, int | str]:
        async with self._lock:
            current = self.local_now(now)
            if not self.in_delivery_window(current):
                return {"status": "outside_window", "sent": 0, "failed": 0}

            day = current.date()
            for group_id in self.group_ids:
                self.database.ensure_a_coast_daily_ranking_delivery(day, group_id)
            pending = self.database.pending_a_coast_daily_ranking_deliveries(day)
            if not pending:
                return {"status": "already_delivered", "sent": 0, "failed": 0}

            target = bot or next(iter(get_bots().values()), None)
            if target is None:
                return {"status": "no_bot", "sent": 0, "failed": 0}

            rows = self.stats_service.ranking_rows("day")
            fallback = self.stats_service.render_rows(rows, TITLE)
            avatar_paths = await self.avatar_service.prefetch(rows)
            group_totals = self.stats_service.group_totals("day")
            group_avatar_paths: dict[int, Any] = {}
            if self.group_avatar_service is not None:
                group_avatar_paths = await self.group_avatar_service.prefetch(
                    [{"user_id": int(row["group_id"])} for row in group_totals]
                )
            try:
                poster = self.report_renderer.render_ranking(
                    rows,
                    TITLE,
                    SUBTITLE,
                    avatar_paths,
                    show_group_labels=True,
                    group_totals=group_totals,
                    group_avatar_paths=group_avatar_paths,
                )
                message: Any = local_image_segment(poster)
            except Exception:
                logger.exception("Unable to render scheduled A海岸 daily ranking; using text fallback")
                message = fallback

            sent = 0
            failed = 0
            uncertain = 0
            for row in pending:
                group_id = int(row["group_id"])
                try:
                    await call_qq_action(target, "send_group_msg", group_id=group_id, message=message)
                except ActionFailed as exc:
                    # A timed-out OneBot action can still have reached QQ. Avoid duplicate posters.
                    uncertain += 1
                    self.database.mark_a_coast_daily_ranking_delivery_uncertain(
                        day, group_id, type(exc).__name__
                    )
                    logger.warning(
                        "Scheduled A海岸 daily ranking submission is unconfirmed "
                        "(day={}, group_id={}, error={})",
                        day,
                        group_id,
                        type(exc).__name__,
                    )
                except Exception as exc:
                    failed += 1
                    self.database.mark_a_coast_daily_ranking_delivery_error(
                        day, group_id, type(exc).__name__
                    )
                    logger.warning(
                        "Scheduled A海岸 daily ranking failed; it will retry in this window "
                        "(day={}, group_id={}, error={})",
                        day,
                        group_id,
                        type(exc).__name__,
                    )
                else:
                    sent += 1
                    self.database.mark_a_coast_daily_ranking_delivery_sent(day, group_id)
                    logger.info("Scheduled A海岸 daily ranking sent (day={}, group_id={})", day, group_id)
            return {"status": "processed", "sent": sent, "failed": failed, "uncertain": uncertain}
