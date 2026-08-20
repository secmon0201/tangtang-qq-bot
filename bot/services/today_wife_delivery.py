"""23:50 delivery coordinator for 今日缘分 daily conclusions."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import Any, Callable, Iterable

from nonebot import get_bots, logger

from bot.services.media import local_image_segment
from bot.services.qq_platform import call_qq_action


WINDOW_START = time(23, 50)
WINDOW_END = time(23, 59, 59, 999999)


class TodayWifeConclusionDelivery:
    def __init__(
        self,
        game_service: Any,
        renderer: Any,
        enabled_groups: Callable[[], Iterable[int]],
    ) -> None:
        self.game_service = game_service
        self.renderer = renderer
        self.enabled_groups = enabled_groups
        self._lock = asyncio.Lock()

    def in_window(self, now: datetime) -> bool:
        local = self.game_service.local_now(now)
        return WINDOW_START <= local.timetz().replace(tzinfo=None) <= WINDOW_END

    async def deliver_once(self, bot: Any | None = None, now: datetime | None = None) -> dict[str, int | str]:
        async with self._lock:
            current = self.game_service.local_now(now)
            if not self.in_window(current):
                return {"status": "outside_window", "sent": 0, "failed": 0}
            target = bot or next(iter(get_bots().values()), None)
            if target is None:
                return {"status": "no_onebot_connection", "sent": 0, "failed": 0}
            group_ids = tuple(dict.fromkeys(int(group_id) for group_id in self.enabled_groups()))
            if not group_ids:
                return {"status": "no_enabled_groups", "sent": 0, "failed": 0}
            prepared: dict[int, dict[str, Any]] = {}
            for group_id in group_ids:
                # No one entered: there is no story to premiere, and therefore
                # no empty poster to disturb the group with.
                story = self.game_service.group_story(group_id, current)
                if not story["records"]:
                    continue
                prepared[group_id] = self.game_service.lock_and_conclude(group_id, current)
            sent = failed = 0
            for group_id, state in prepared.items():
                if state.get("delivered_at"):
                    continue
                try:
                    path = self.renderer.render_today_wife_conclusion(state.get("conclusion") or {})
                    await call_qq_action(target, "send_group_msg", group_id=group_id, message=local_image_segment(path))
                except Exception as exc:
                    failed += 1
                    self.game_service.mark_conclusion_delivered(group_id, current, type(exc).__name__)
                    logger.warning("Today-wife conclusion will retry in this window (group={}, error={})", group_id, type(exc).__name__)
                else:
                    sent += 1
                    self.game_service.mark_conclusion_delivered(group_id, current)
                    logger.info("Today-wife conclusion delivered (group={})", group_id)
            return {"status": "processed", "sent": sent, "failed": failed}
