from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11.exception import ActionFailed

from bot.config import RESOURCE_DIR, settings
from bot.db import Database
from bot.services.hourly_copy import HourlyCopyCatalog
from bot.services.qq_platform import call_qq_action


RETRY_WINDOW_SECONDS = 5 * 60
ENABLED_KEY = "enabled"
START_MINUTE_KEY = "start_minute"
END_MINUTE_KEY = "end_minute"
PERIOD_KEYS = ("morning", "daytime", "evening", "night")
PERIOD_SHORT_LABELS = {
    "morning": "早",
    "daytime": "白",
    "evening": "晚",
    "night": "夜",
}
NIGHT_SEND_HOURS = frozenset({0, 2, 4, 22, 23})
RECENT_TEXT_WINDOW = 24


@dataclass(frozen=True, slots=True)
class HourlyAnnouncementConfig:
    enabled: bool
    start_minute: int
    end_minute: int
    max_attempts: int


class HourlyAnnouncementService:
    """Compose and deliver one persisted announcement per group and hour."""

    def __init__(
        self,
        database: Database,
        group_ids: Callable[[], Iterable[int]],
        texts: Iterable[str] | None = None,
        content_path: Path | None = None,
        alias_path: Path | None = None,
    ) -> None:
        self.database = database
        self.group_ids = group_ids
        self._lock = asyncio.Lock()
        self.content_path = content_path or RESOURCE_DIR / "zhijiang_hourly_copy.json"
        self.alias_path = alias_path or RESOURCE_DIR / "zhijiang_character_aliases.json"
        self.catalog: HourlyCopyCatalog | None = None
        self.text_pools: dict[str, tuple[str, ...]] = {}
        self.texts: tuple[str, ...] = ()
        if texts is not None:
            self.text_pools = {"all": self._validate_texts(texts)}
            self.texts = self.text_pools["all"]
        else:
            self.catalog = HourlyCopyCatalog.load(self.content_path, self.alias_path)
        self._config = self._load_config()

    @staticmethod
    def _validate_texts(raw_texts: Iterable[str]) -> tuple[str, ...]:
        raw = tuple(raw_texts)
        texts = tuple(
            text.strip()
            for text in raw
            if isinstance(text, str) and text.strip()
        )
        if len(texts) != len(raw):
            raise ValueError("hourly announcement text pool contains an empty or invalid text")
        if len(set(texts)) != len(texts):
            raise ValueError("hourly announcement text pool contains duplicate texts")
        return texts

    @staticmethod
    def _boolean(value: str | None, default: bool) -> bool:
        normalized = str(value if value is not None else default).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _minute(value: str | None, default: int) -> int:
        try:
            parsed = int(value) if value is not None else default
        except (TypeError, ValueError):
            return default
        return parsed if 0 <= parsed <= 1439 else default

    def _load_config(self) -> HourlyAnnouncementConfig:
        defaults = {
            ENABLED_KEY: str(getattr(settings, "hourly_announcement_enabled", False)).lower(),
            START_MINUTE_KEY: str(getattr(settings, "hourly_announcement_start_minute", 0)),
            END_MINUTE_KEY: str(getattr(settings, "hourly_announcement_end_minute", 1380)),
        }
        values = self.database.hourly_announcement_settings()
        for key, value in defaults.items():
            if key not in values:
                self.database.set_hourly_announcement_setting(key, value)
                values[key] = value
        return HourlyAnnouncementConfig(
            enabled=self._boolean(values.get(ENABLED_KEY), False),
            start_minute=self._minute(values.get(START_MINUTE_KEY), 0),
            end_minute=self._minute(values.get(END_MINUTE_KEY), 1380),
            max_attempts=int(getattr(settings, "hourly_announcement_max_attempts", 3)),
        )

    @property
    def config(self) -> HourlyAnnouncementConfig:
        return self._config

    def set_enabled(self, enabled: bool) -> HourlyAnnouncementConfig:
        self.database.set_hourly_announcement_setting(ENABLED_KEY, str(bool(enabled)).lower())
        self._config = HourlyAnnouncementConfig(
            enabled=bool(enabled),
            start_minute=self._config.start_minute,
            end_minute=self._config.end_minute,
            max_attempts=self._config.max_attempts,
        )
        return self._config

    def set_schedule(self, start_minute: int, end_minute: int) -> HourlyAnnouncementConfig:
        if not 0 <= int(start_minute) <= 1439 or not 0 <= int(end_minute) <= 1439:
            raise ValueError("hourly announcement schedule must be within 00:00-23:59")
        self.database.set_hourly_announcement_setting(START_MINUTE_KEY, str(int(start_minute)))
        self.database.set_hourly_announcement_setting(END_MINUTE_KEY, str(int(end_minute)))
        self._config = HourlyAnnouncementConfig(
            enabled=self._config.enabled,
            start_minute=int(start_minute),
            end_minute=int(end_minute),
            max_attempts=self._config.max_attempts,
        )
        return self._config

    def group_ids_snapshot(self) -> tuple[int, ...]:
        return tuple(sorted({int(group_id) for group_id in self.group_ids()}))

    @staticmethod
    def format_clock(minutes: int) -> str:
        return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"

    @staticmethod
    def _compact_count(value: int) -> str:
        if value >= 100_000_000:
            number = f"{value / 100_000_000:.2f}".rstrip("0").rstrip(".")
            return f"{number}亿"
        if value >= 10_000:
            number = f"{value / 10_000:.2f}".rstrip("0").rstrip(".")
            return f"{number}万"
        return str(value)

    def copy_pool_summary(self) -> str:
        if self.catalog is None:
            return f"all {len(self.texts)} 条"
        counts = self.catalog.combination_counts()
        fragments = " ".join(
            f"{PERIOD_SHORT_LABELS[key]}"
            f"{len(self.catalog.periods[key].segments['before'].texts)}/"
            f"{len(self.catalog.periods[key].segments['middle'].texts)}/"
            f"{len(self.catalog.periods[key].segments['after'].texts)}"
            for key in PERIOD_KEYS
        )
        return f"前/中/后：{fragments}；组合约{self._compact_count(sum(counts.values()))}"

    def schedule_text(self) -> str:
        return (
            f"状态：{'开启' if self.config.enabled else '关闭'}；"
            f"时段：{self.format_clock(self.config.start_minute)}-"
            f"{self.format_clock(self.config.end_minute)}；"
            f"素材池：{self.copy_pool_summary()}"
        )

    def group_detail(self, group_id: int) -> str:
        return (
            f"整点报时：{'已加入' if int(group_id) in self.group_ids_snapshot() else '未加入'}。\n"
            f"{self.schedule_text()}"
        )

    def _local_now(self, now: datetime | None = None) -> datetime:
        zone = ZoneInfo(settings.timezone)
        if now is None:
            return datetime.now(zone)
        if now.tzinfo is None:
            return now.replace(tzinfo=zone)
        return now.astimezone(zone)

    def _within_schedule(self, now: datetime) -> bool:
        minute = now.hour * 60 + now.minute
        start = self.config.start_minute
        end = self.config.end_minute
        if start <= end:
            return start <= minute <= end
        return minute >= start or minute <= end

    @staticmethod
    def slot_key(now: datetime) -> str:
        return now.strftime("%Y-%m-%dT%H:00%z")

    @staticmethod
    def _period_key(now: datetime) -> str:
        minute = now.hour * 60 + now.minute
        if 360 <= minute < 600:
            return "morning"
        if 600 <= minute < 1020:
            return "daytime"
        if 1020 <= minute < 1320:
            return "evening"
        return "night"

    def _pool_for_time(self, now: datetime) -> tuple[str, tuple[str, ...] | None]:
        period = self._period_key(now)
        if period in self.text_pools:
            return period, self.text_pools[period]
        if self.catalog is not None and period in self.catalog.periods:
            return period, None
        return "all", self.text_pools["all"]

    @classmethod
    def _is_send_hour(cls, now: datetime) -> bool:
        return cls._period_key(now) != "night" or now.hour in NIGHT_SEND_HOURS

    def _text_index(
        self,
        slot_key: str,
        group_id: int,
        pool_key: str = "all",
        blocked: Iterable[int] = (),
    ) -> int:
        pool = self.text_pools[pool_key]
        digest = hashlib.sha256(f"{slot_key}:{int(group_id)}".encode("utf-8")).digest()
        base = int.from_bytes(digest[:8], "big") % len(pool)
        blocked_indexes = {int(index) for index in blocked}
        for offset in range(len(pool)):
            candidate = (base + offset) % len(pool)
            if candidate not in blocked_indexes:
                return candidate
        return base

    async def deliver_once(self, bot: Any | None = None, now: datetime | None = None) -> dict[str, int | str]:
        async with self._lock:
            current = self._local_now(now)
            if not self.config.enabled or not self._within_schedule(current):
                return {"status": "outside_schedule", "sent": 0, "failed": 0}
            if not self._is_send_hour(current):
                return {"status": "night_hour_skipped", "sent": 0, "failed": 0}
            seconds_since_hour = current.minute * 60 + current.second
            if current.minute != 0 and seconds_since_hour > RETRY_WINDOW_SECONDS:
                return {"status": "retry_window_closed", "sent": 0, "failed": 0}
            target = bot
            if target is None:
                bots = get_bots()
                target = next(iter(bots.values()), None) if bots else None
            if target is None:
                return {"status": "no_bot", "sent": 0, "failed": 0}

            slot = self.slot_key(current)
            pool_key, pool = self._pool_for_time(current)
            groups = self.group_ids_snapshot()
            for group_id in groups:
                recent_indexes = self.database.recent_hourly_text_indices(
                    group_id, RECENT_TEXT_WINDOW
                )
                if self.catalog is not None:
                    generated = self.catalog.compose(pool_key, recent_indexes)
                    text_index = generated.fingerprint
                    message = generated.text
                else:
                    if pool is None:
                        raise RuntimeError("hourly announcement text pool is unavailable")
                    text_index = self._text_index(slot, group_id, pool_key, recent_indexes)
                    message = pool[text_index]
                self.database.ensure_hourly_delivery(slot, group_id, text_index, message)

            sent = 0
            failed = 0
            uncertain = 0
            allowed = set(groups)
            for row in self.database.pending_hourly_deliveries(
                slot, self.config.max_attempts, limit=max(1, len(groups))
            ):
                group_id = int(row["group_id"])
                if group_id not in allowed:
                    continue
                text_index = int(row["text_index"])
                message = str(row["message"] or "")
                if not message:
                    # Rows created before the segmented source migration have no
                    # persisted message. Generate one once, then keep it stable.
                    if self.catalog is not None:
                        message = self.catalog.compose(pool_key).text
                    elif pool is not None and 0 <= text_index < len(pool):
                        message = pool[text_index]
                    else:
                        self.database.mark_hourly_delivery_error(
                            slot, group_id, "MissingHourlyCopy", self.config.max_attempts
                        )
                        failed += 1
                        continue
                    self.database.update_hourly_delivery_message(slot, group_id, message)
                try:
                    await call_qq_action(
                        target,
                        "send_group_msg",
                        group_id=group_id,
                        message=message,
                    )
                except ActionFailed as exc:
                    # A OneBot gateway can submit a message and then time out waiting for its
                    # local confirmation event. Retrying would duplicate the post.
                    uncertain += 1
                    self.database.mark_hourly_delivery_uncertain(
                        slot, group_id, type(exc).__name__
                    )
                    logger.warning(
                        "Hourly announcement submission is unconfirmed; retry suppressed "
                        "(slot={}, group_id={}, text_index={}, error={})",
                        slot,
                        group_id,
                        text_index,
                        type(exc).__name__,
                    )
                except Exception as exc:
                    failed += 1
                    self.database.mark_hourly_delivery_error(
                        slot, group_id, type(exc).__name__, self.config.max_attempts
                    )
                    logger.warning(
                        "Hourly announcement failed (slot={}, group_id={}, text_index={}, error={})",
                        slot,
                        group_id,
                        text_index,
                        type(exc).__name__,
                    )
                else:
                    sent += 1
                    self.database.mark_hourly_delivery_sent(slot, group_id)
                    logger.info(
                        "Hourly announcement sent (slot={}, group_id={}, text_index={})",
                        slot,
                        group_id,
                        text_index,
                    )
            return {
                "status": "processed",
                "sent": sent,
                "failed": failed,
                "uncertain": uncertain,
            }
