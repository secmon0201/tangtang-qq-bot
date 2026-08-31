"""Staggered delivery coordinator for 今日缘分 collective rounds."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, time, timedelta
from typing import Any, Callable, Iterable, Mapping

from nonebot import get_bots, logger

from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action


ROUND_WINDOWS = (
    (1, time(11, 30), time(12, 0)),
    (2, time(17, 30), time(18, 0)),
    (3, time(23, 0), time(23, 30)),
)
DELIVERY_TAIL_BUFFER_SECONDS = 60


class TodayWifeRoundDelivery:
    def __init__(
        self,
        game_service: Any,
        renderer: Any,
        enabled_groups: Callable[[], Iterable[int]],
        group_locks: Mapping[int, asyncio.Lock],
    ) -> None:
        self.game_service = game_service
        self.renderer = renderer
        self.enabled_groups = enabled_groups
        self.group_locks = group_locks
        self._lock = asyncio.Lock()

    def active_window(self, now: datetime) -> tuple[int, time, time] | None:
        local = self.game_service.local_now(now)
        value = local.timetz().replace(tzinfo=None)
        return next((item for item in ROUND_WINDOWS if item[1] <= value < item[2]), None)

    @staticmethod
    def scheduled_at(
        current: datetime,
        round_no: int,
        start: time,
        end: time,
        group_ids: tuple[int, ...],
        group_id: int,
    ) -> datetime:
        """Spread groups evenly through the window in a stable daily order."""

        ordered = sorted(
            group_ids,
            key=lambda value: hashlib.blake2s(
                f"{current.date().isoformat()}:{round_no}:{value}".encode("ascii"),
                digest_size=8,
            ).digest(),
        )
        index = ordered.index(int(group_id))
        window_start = current.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
        window_end = current.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        seconds = max(1, int((window_end - window_start).total_seconds()))
        spread_seconds = max(1, seconds - DELIVERY_TAIL_BUFFER_SECONDS)
        offset = min(spread_seconds - 1, (index * spread_seconds) // max(1, len(ordered)))
        return window_start + timedelta(seconds=offset)

    async def deliver_once(self, bot: Any | None = None, now: datetime | None = None) -> dict[str, int | str]:
        async with self._lock:
            current = self.game_service.local_now(now)
            active_window = self.active_window(current)
            if active_window is None:
                return {"status": "outside_window", "sent": 0, "failed": 0}
            target = bot or next(iter(get_bots().values()), None)
            if target is None:
                return {"status": "no_onebot_connection", "sent": 0, "failed": 0}
            configured = tuple(dict.fromkeys(int(group_id) for group_id in self.enabled_groups()))
            active_groups = tuple(
                group_id
                for group_id in configured
                if self.game_service.collective_participant_count(group_id, current) > 0
            )
            if not active_groups:
                return {"status": "no_active_groups", "sent": 0, "failed": 0}

            round_no, start, end = active_window
            sent = failed = 0
            for group_id in active_groups:
                due_at = self.scheduled_at(current, round_no, start, end, active_groups, group_id)
                if current < due_at or self.game_service.collective_round_delivered(group_id, round_no, current):
                    continue
                async with self.group_locks[group_id]:
                    payload = self.game_service.prepare_collective_round(group_id, round_no, current)
                    if payload is None:
                        continue
                    try:
                        path = self.renderer.render_today_wife_collective_round(payload)
                        await call_qq_action(
                            target,
                            "send_group_msg",
                            group_id=group_id,
                            message=local_image_segment(path),
                        )
                    except Exception as exc:
                        failed += 1
                        self.game_service.mark_collective_round_delivery(
                            group_id,
                            round_no,
                            current,
                            error=type(exc).__name__,
                        )
                        logger.warning(
                            "Today-wife collective round will retry (group={}, round={}, error={})",
                            group_id,
                            round_no,
                            type(exc).__name__,
                        )
                    else:
                        sent += 1
                        self.game_service.mark_collective_round_delivery(group_id, round_no, current)
                        logger.info(
                            "Today-wife collective round delivered (group={}, round={})",
                            group_id,
                            round_no,
                        )
            return {"status": "processed", "sent": sent, "failed": failed}


TodayWifeConclusionDelivery = TodayWifeRoundDelivery
