from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from bot.db import Database
from bot.services.passive_settings import PassiveSettingsStore


CACHE_KEY = "zhijiang_live_schedule_cache"
ETAG_KEY = "zhijiang_live_schedule_etag"
LAST_REFRESH_KEY = "zhijiang_live_schedule_last_refresh"
LAST_ERROR_KEY = "zhijiang_live_schedule_last_error"
PAUSED_UNTIL_KEY = "zhijiang_live_game_paused_until"
SEEN_EVENTS_KEY = "zhijiang_live_game_seen_events"
SEEN_EVENT_RETENTION = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class LiveSchedule:
    event_id: str
    starts_at: datetime
    category: str
    title: str
    live_room_url: str
    event_type: str

    @classmethod
    def from_source(cls, item: Any, timezone: ZoneInfo) -> "LiveSchedule | None":
        if not isinstance(item, dict):
            return None
        date_value = str(item.get("date") or "").strip()
        time_value = str(item.get("time") or "").strip()
        room_url = str(item.get("liveRoomUrl") or "").strip()
        parsed_url = urlparse(room_url)
        if parsed_url.hostname != "live.bilibili.com":
            return None
        try:
            starts_at = datetime.strptime(
                f"{date_value} {time_value}", "%Y/%m/%d %H:%M"
            ).replace(tzinfo=timezone)
        except ValueError:
            return None
        category = str(item.get("category") or "其他").strip()[:80]
        title = str(item.get("title") or "未命名直播").strip()[:200]
        event_type = str(item.get("type") or "直播").strip()[:40]
        raw_id = str(item.get("id") or "").strip()
        if not raw_id:
            raw_id = hashlib.sha256(
                f"{starts_at.isoformat()}|{category}|{title}|{room_url}".encode("utf-8")
            ).hexdigest()[:24]
        return cls(raw_id[:240], starts_at, category, title, room_url, event_type)

    @classmethod
    def from_cache(cls, item: Any, timezone: ZoneInfo) -> "LiveSchedule | None":
        if not isinstance(item, dict):
            return None
        try:
            starts_at = datetime.fromisoformat(str(item["starts_at"]))
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=timezone)
            else:
                starts_at = starts_at.astimezone(timezone)
            return cls(
                str(item["event_id"]),
                starts_at,
                str(item.get("category") or "其他"),
                str(item.get("title") or "未命名直播"),
                str(item.get("live_room_url") or ""),
                str(item.get("event_type") or "直播"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def as_cache(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "starts_at": self.starts_at.isoformat(),
            "category": self.category,
            "title": self.title,
            "live_room_url": self.live_room_url,
            "event_type": self.event_type,
        }


@dataclass(frozen=True, slots=True)
class LiveGuardStatus:
    enabled: bool
    source_url: str
    last_refresh: str | None
    last_error: str | None
    paused_until: datetime | None
    global_game_enabled: bool
    upcoming: tuple[LiveSchedule, ...]


class ZhijiangLiveGuard:
    """Keep the game's global switch aligned with public Bilibili live schedules.

    The source is a community-maintained schedule, so an invalid or unavailable
    response never changes the current switch. A local cache covers a restart or
    temporary source outage during an already-known live window.
    """

    def __init__(
        self,
        database: Database,
        feature_scopes: PassiveSettingsStore,
        *,
        enabled: bool,
        source_url: str,
        timezone: str,
        refresh_minutes: int = 5,
        pause_minutes: int = 60,
        lookahead_days: int = 7,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.database = database
        self.feature_scopes = feature_scopes
        self.enabled = bool(enabled)
        self.source_url = source_url.strip()
        self.timezone = ZoneInfo(timezone)
        self.refresh_minutes = int(refresh_minutes)
        self.pause_duration = timedelta(minutes=int(pause_minutes))
        self.lookahead_days = int(lookahead_days)
        self.timeout_seconds = float(timeout_seconds)
        self._lock = asyncio.Lock()
        self.entries = self._read_cached_entries()

    def _settings(self) -> dict[str, str]:
        return self.database.passive_settings()

    def _read_cached_entries(self) -> tuple[LiveSchedule, ...]:
        raw = self._settings().get(CACHE_KEY, "[]")
        try:
            items = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return ()
        if not isinstance(items, list):
            return ()
        entries = [LiveSchedule.from_cache(item, self.timezone) for item in items]
        return tuple(sorted((entry for entry in entries if entry is not None), key=lambda entry: entry.starts_at))

    def _write_entries(self, entries: tuple[LiveSchedule, ...]) -> None:
        self.entries = entries
        self.database.set_passive_setting(
            CACHE_KEY,
            json.dumps([entry.as_cache() for entry in entries], ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _parse_moment(value: str | None, timezone: ZoneInfo) -> datetime | None:
        if not value:
            return None
        try:
            result = datetime.fromisoformat(value)
        except ValueError:
            return None
        return result.replace(tzinfo=timezone) if result.tzinfo is None else result.astimezone(timezone)

    def _read_seen_events(self, now: datetime) -> dict[str, datetime]:
        raw = self._settings().get(SEEN_EVENTS_KEY, "{}")
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        cutoff = now - SEEN_EVENT_RETENTION
        seen: dict[str, datetime] = {}
        for event_id, value in data.items():
            moment = self._parse_moment(str(value), self.timezone)
            if moment is not None and moment >= cutoff:
                seen[str(event_id)] = moment
        return seen

    def _write_seen_events(self, seen: dict[str, datetime]) -> None:
        self.database.set_passive_setting(
            SEEN_EVENTS_KEY,
            json.dumps(
                {event_id: value.isoformat() for event_id, value in seen.items()},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    def _normalise_entries(self, data: Any, now: datetime) -> tuple[LiveSchedule, ...]:
        if not isinstance(data, dict) or not isinstance(data.get("schedules"), list):
            raise ValueError("schedule payload must contain a schedules array")
        earliest = now - self.pause_duration
        latest = now + timedelta(days=self.lookahead_days)
        unique: dict[str, LiveSchedule] = {}
        for item in data["schedules"]:
            entry = LiveSchedule.from_source(item, self.timezone)
            if entry is None or not earliest < entry.starts_at <= latest:
                continue
            unique[entry.event_id] = entry
        return tuple(sorted(unique.values(), key=lambda entry: (entry.starts_at, entry.event_id)))

    async def refresh(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        now = now or datetime.now(self.timezone)
        settings = self._settings()
        headers = {"Accept": "application/json", "Cache-Control": "no-cache"}
        if etag := settings.get(ETAG_KEY):
            headers["If-None-Match"] = etag
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
                response = await client.get(self.source_url, headers=headers)
            if response.status_code == 304:
                self.database.set_passive_setting(LAST_REFRESH_KEY, now.isoformat())
                self.database.set_passive_setting(LAST_ERROR_KEY, "")
                return self.entries
            response.raise_for_status()
            entries = self._normalise_entries(response.json(), now)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            message = f"{type(exc).__name__}: {exc}"[:500]
            self.database.set_passive_setting(LAST_ERROR_KEY, message)
            raise RuntimeError(message) from exc
        self._write_entries(entries)
        self.database.set_passive_setting(LAST_REFRESH_KEY, now.isoformat())
        self.database.set_passive_setting(LAST_ERROR_KEY, "")
        if etag := response.headers.get("ETag"):
            self.database.set_passive_setting(ETAG_KEY, etag)
        return entries

    def apply_due(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        now = now or datetime.now(self.timezone)
        seen = self._read_seen_events(now)
        newly_started = tuple(
            entry
            for entry in self.entries
            if now - self.pause_duration < entry.starts_at <= now and entry.event_id not in seen
        )
        if newly_started:
            latest_start = max(entry.starts_at for entry in newly_started)
            paused_until = latest_start + self.pause_duration
            self.database.set_passive_setting(PAUSED_UNTIL_KEY, paused_until.isoformat())
            for entry in newly_started:
                seen[entry.event_id] = now
            self._write_seen_events(seen)

        paused_until = self._parse_moment(
            self._settings().get(PAUSED_UNTIL_KEY), self.timezone
        )
        if paused_until is not None and paused_until <= now:
            self.database.set_passive_setting(PAUSED_UNTIL_KEY, "")
        return newly_started

    async def refresh_and_apply(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        """Refresh first, then apply. Cached data remains usable on refresh errors."""
        async with self._lock:
            current = now or datetime.now(self.timezone)
            if not self.enabled:
                return ()
            try:
                await self.refresh(current)
            except RuntimeError:
                # A failed refresh must not disable the games or discard the cache.
                pass
            return self.apply_due(current)

    async def tick_async(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        async with self._lock:
            return self.tick(now)

    def tick(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        if not self.enabled:
            return ()
        return self.apply_due(now)

    def active_entries(self, now: datetime | None = None) -> tuple[LiveSchedule, ...]:
        """Return every distinct stream that started in the active game pause window."""
        if not self.enabled:
            return ()
        current = now or datetime.now(self.timezone)
        paused_until = self._parse_moment(
            self._settings().get(PAUSED_UNTIL_KEY), self.timezone
        )
        if paused_until is None or paused_until <= current:
            return ()
        earliest = current - self.pause_duration
        active = {
            entry.event_id: entry
            for entry in self.entries
            if earliest < entry.starts_at <= current
        }
        return tuple(sorted(active.values(), key=lambda entry: (entry.starts_at, entry.event_id)))

    def status(self, now: datetime | None = None) -> LiveGuardStatus:
        current = now or datetime.now(self.timezone)
        values = self._settings()
        horizon = current + timedelta(days=self.lookahead_days)
        upcoming = tuple(entry for entry in self.entries if current < entry.starts_at <= horizon)
        return LiveGuardStatus(
            enabled=self.enabled,
            source_url=self.source_url,
            last_refresh=values.get(LAST_REFRESH_KEY) or None,
            last_error=values.get(LAST_ERROR_KEY) or None,
            paused_until=self._parse_moment(values.get(PAUSED_UNTIL_KEY), self.timezone),
            global_game_enabled=self.feature_scopes.is_game_globally_enabled(),
            upcoming=upcoming,
        )
