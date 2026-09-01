from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11.exception import ActionFailed

from bot.config import settings
from bot.db import Database
from bot.services.community_web import (
    DOMAIN_GROUP_KEY,
    public_domain_ranking_url,
    ranking_payload,
)
from bot.services.group_domains import GroupDomain, GroupDomainService
from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action


WINDOW_START = time(23, 50)
CATCHUP_END = time(0, 30, 59, 999999)
DELIVERY_TYPE = "speech-ranking-2350"


class DomainDailyRankingDeliveryService:
    """Deliver one persistent daily ranking job for every opted-in QQ group."""

    def __init__(
        self,
        database: Database,
        stats_service: Any,
        domains: GroupDomainService,
        avatar_service: Any,
        report_renderer: Any,
        *,
        group_avatar_service: Any | None = None,
        community_renderer: Any | None = None,
    ) -> None:
        self.database = database
        self.stats_service = stats_service
        self.domains = domains
        self.avatar_service = avatar_service
        self.report_renderer = report_renderer
        self.group_avatar_service = group_avatar_service
        self.community_renderer = community_renderer
        self.zone = ZoneInfo(settings.timezone)
        self._lock = asyncio.Lock()

    def local_now(self, now: datetime | None = None) -> datetime:
        if now is None:
            return datetime.now(self.zone)
        if now.tzinfo is None:
            return now.replace(tzinfo=self.zone)
        return now.astimezone(self.zone)

    @staticmethod
    def delivery_day(now: datetime):
        value = now.timetz().replace(tzinfo=None)
        if value >= WINDOW_START:
            return now.date()
        if value <= CATCHUP_END:
            return now.date() - timedelta(days=1)
        return None

    async def deliver_once(
        self, bot: Any | None = None, now: datetime | None = None
    ) -> dict[str, int | str]:
        async with self._lock:
            current = self.local_now(now)
            day = self.delivery_day(current)
            if day is None:
                return {"status": "outside_window", "sent": 0, "failed": 0}

            target_groups = self.domains.enabled_groups("speech_ranking_push")
            for group_id in sorted(target_groups):
                domain = self.domains.domain_for_group(group_id)
                if domain is not None:
                    self.database.ensure_ranking_delivery(
                        day, group_id, DELIVERY_TYPE, domain.domain_id
                    )
            pending = [
                row
                for row in self.database.pending_ranking_deliveries(day, DELIVERY_TYPE)
                if int(row["group_id"]) in target_groups
            ]
            if not pending:
                return {"status": "already_delivered", "sent": 0, "failed": 0}

            target = bot or next(iter(get_bots().values()), None)
            if target is None:
                return {"status": "no_bot", "sent": 0, "failed": 0}

            messages: dict[int, Message] = {}
            sent = failed = uncertain = 0
            for row in pending:
                group_id = int(row["group_id"])
                domain = self.domains.domain_for_group(group_id)
                if domain is None:
                    continue
                try:
                    message = messages.get(domain.domain_id)
                    if message is None:
                        message = await self._domain_message(domain)
                        messages[domain.domain_id] = message
                    await call_qq_action(
                        target, "send_group_msg", group_id=group_id, message=message
                    )
                except ActionFailed as exc:
                    uncertain += 1
                    self.database.mark_ranking_delivery_uncertain(
                        day,
                        group_id,
                        DELIVERY_TYPE,
                        type(exc).__name__,
                    )
                except Exception as exc:
                    failed += 1
                    self.database.mark_ranking_delivery_error(
                        day,
                        group_id,
                        DELIVERY_TYPE,
                        type(exc).__name__,
                    )
                    logger.warning(
                        "Scheduled ranking failed and remains pending "
                        "(day={}, group_id={}, error={})",
                        day,
                        group_id,
                        type(exc).__name__,
                    )
                else:
                    sent += 1
                    self.database.mark_ranking_delivery_sent(
                        day, group_id, DELIVERY_TYPE
                    )
            return {
                "status": "processed",
                "sent": sent,
                "failed": failed,
                "uncertain": uncertain,
            }

    async def _domain_message(self, domain: GroupDomain) -> Message:
        group_ids = self.domains.domain_groups(domain.domain_id)
        rows = self.stats_service.ranking_rows_for_groups("day", group_ids)
        avatars = await self.avatar_service.prefetch(rows)
        group_totals = (
            self.stats_service.group_totals_for_groups("day", group_ids)
            if domain.mode == "cluster"
            else []
        )
        group_avatars: dict[int, Any] = {}
        if group_totals and self.group_avatar_service is not None:
            group_avatars = await self.group_avatar_service.prefetch(
                [{"user_id": int(row["group_id"])} for row in group_totals]
            )
        labels = {
            group_id: self.domains.display_name(group_id) for group_id in group_ids
        }
        domain_label = self.domains.domain_display_name(domain)
        options = [{"key": DOMAIN_GROUP_KEY, "label": domain_label}]
        if domain.mode == "cluster":
            options.extend(
                {
                    "key": self.domains.public_group_key(group_id) or "",
                    "label": labels[group_id],
                }
                for group_id in group_ids
            )
        payload = ranking_payload(
            self.stats_service,
            "day",
            DOMAIN_GROUP_KEY,
            rows=rows,
            avatar_paths=avatars,
            group_totals=group_totals,
            group_avatar_paths=group_avatars,
            group_label_override=domain_label,
            group_labels=labels,
            group_options=options,
            history_since=self.domains.domain_joined_date(domain.domain_id),
        )
        poster = None
        if self.community_renderer is not None:
            try:
                poster = await self.community_renderer.render_ranking(payload)
            except Exception:
                logger.exception("Unable to render scheduled ranking with HTML")
        title = f"{domain_label}今日发言榜"
        subtitle = str(payload["subtitle"])
        if poster is None:
            try:
                poster = self.report_renderer.render_ranking(
                    rows,
                    title,
                    subtitle,
                    avatars,
                    show_group_labels=domain.mode == "cluster",
                    group_totals=group_totals,
                    group_avatar_paths=group_avatars,
                )
            except Exception:
                logger.exception("Unable to render scheduled ranking; using text")
        message = (
            Message(local_image_segment(poster))
            if poster is not None
            else Message(self.stats_service.render_rows(rows, title))
        )
        link = public_domain_ranking_url(domain.public_token)
        if link:
            message += f"\n在线：{link}"
        return message


__all__ = ["DELIVERY_TYPE", "DomainDailyRankingDeliveryService"]
