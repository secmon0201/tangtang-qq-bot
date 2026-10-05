"""Smart Cache Warmer for Agent context TTL maintenance and cold-start elimination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from bot.services.agent_context import ContextEnvelope
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider, TangtangService


DEFAULT_TIMEZONE = ZoneInfo("Asia/Shanghai")
WARMUP_WINDOW_START = time(9, 0)
WARMUP_WINDOW_END = time(23, 30)
DEFAULT_WARMUP_INTERVAL_SECONDS = 360  # 6 minutes
DEFAULT_DAILY_WARMUP_QUOTA = 140
CIRCUIT_BREAK_FAILURE_THRESHOLD = 2
CIRCUIT_BREAK_PAUSE_MINUTES = 30


@dataclass(frozen=True, slots=True)
class WarmupTarget:
    group_id: int
    cache_affinity_key: str
    static_prefix_hash: str


@dataclass(frozen=True, slots=True)
class WarmupResult:
    group_id: int
    success: bool
    status: str
    cache_read_tokens: int = 0
    non_cached_input_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    error: str | None = None


def is_warmup_window_active(dt: datetime | None = None) -> bool:
    """Return True if current time is within active warming hours (09:00 - 23:30)."""
    current_dt = dt or datetime.now(DEFAULT_TIMEZONE)
    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=DEFAULT_TIMEZONE)
    else:
        current_dt = current_dt.astimezone(DEFAULT_TIMEZONE)
    current_time = current_dt.time()
    return WARMUP_WINDOW_START <= current_time <= WARMUP_WINDOW_END


class AgentCacheWarmer:
    """Proactively keep active group context caches warm during peak hours."""

    def __init__(
        self,
        service: TangtangService,
        *,
        interval_seconds: int = DEFAULT_WARMUP_INTERVAL_SECONDS,
        daily_quota: int = DEFAULT_DAILY_WARMUP_QUOTA,
        failure_threshold: int = CIRCUIT_BREAK_FAILURE_THRESHOLD,
        pause_duration: timedelta = timedelta(minutes=CIRCUIT_BREAK_PAUSE_MINUTES),
    ) -> None:
        self.service = service
        self.interval_seconds = max(60, int(interval_seconds))
        self.daily_quota = max(1, int(daily_quota))
        self.failure_threshold = max(1, int(failure_threshold))
        self.pause_duration = pause_duration

        self._last_warmed_at: dict[int, datetime] = {}
        self._daily_counts: dict[tuple[int, str], int] = {}
        self._consecutive_failures: dict[int, int] = {}
        self._paused_until: dict[int, datetime] = {}

    def get_persona(self, config: TangtangConfig) -> str:
        try:
            return self.service._persona_text()
        except Exception:
            return "你是达妮娅。"

    def get_group_prefix(self, group_id: int, config: TangtangConfig) -> str:
        try:
            return self.service._stable_group_prefix(group_id, config)
        except Exception:
            return f"[当前群号：{group_id}]"

    def is_group_circuit_open(self, group_id: int, now: datetime) -> bool:
        paused = self._paused_until.get(group_id)
        if paused is None:
            return False
        return now < paused

    def get_daily_count(self, group_id: int, now: datetime) -> int:
        day_key = now.strftime("%Y-%m-%d")
        return self._daily_counts.get((group_id, day_key), 0)

    def record_probe_success(self, group_id: int, now: datetime) -> None:
        day_key = now.strftime("%Y-%m-%d")
        self._daily_counts[(group_id, day_key)] = self.get_daily_count(group_id, now) + 1
        self._consecutive_failures[group_id] = 0
        self._paused_until.pop(group_id, None)
        self._last_warmed_at[group_id] = now

    def record_probe_failure(self, group_id: int, now: datetime) -> None:
        failures = self._consecutive_failures.get(group_id, 0) + 1
        self._consecutive_failures[group_id] = failures
        if failures >= self.failure_threshold:
            self._paused_until[group_id] = now + self.pause_duration

    def get_candidate_groups(
        self,
        config: TangtangConfig,
        now: datetime | None = None,
    ) -> tuple[int, ...]:
        """Return group IDs eligible for warmup probing."""
        if not config.enabled or config.context_layout not in {"v2", "shadow"}:
            return ()
        current_now = now or datetime.now(DEFAULT_TIMEZONE)
        if not is_warmup_window_active(current_now):
            return ()

        effective_interval = getattr(config, "cache_warmup_interval_seconds", self.interval_seconds)
        configured = tuple(config.cache_canary_group_ids or config.group_ids)
        candidates: list[int] = []
        for gid in configured:
            if self.is_group_circuit_open(gid, current_now):
                continue
            if self.get_daily_count(gid, current_now) >= self.daily_quota:
                continue
            last = self._last_warmed_at.get(gid)
            if last is None or (current_now - last).total_seconds() >= effective_interval:
                candidates.append(gid)
        return tuple(candidates)

    def build_warmup_envelope(
        self,
        group_id: int,
        config: TangtangConfig,
    ) -> ContextEnvelope:
        """Construct a lightweight warmup envelope for the target group spine."""
        persona = self.get_persona(config)
        group_prefix = self.get_group_prefix(group_id, config)
        cache_key = f"group-{group_id}-spine"
        return ContextEnvelope.create(
            persona=persona,
            conversation_items=(),
            compacted_snapshot="",
            dynamic_status=group_prefix,
            current_input="[保活探测]",
            cache_affinity_key=cache_key,
        )

    async def warm_group(
        self,
        group_id: int,
        config: TangtangConfig,
        *,
        now: datetime | None = None,
    ) -> WarmupResult:
        """Send a single minimal keep-alive probe to keep the group prefix warm."""
        current_now = now or datetime.now(DEFAULT_TIMEZONE)
        if self.is_group_circuit_open(group_id, current_now):
            return WarmupResult(
                group_id=group_id,
                success=False,
                status="skipped_circuit",
                error="circuit_open",
            )
        if self.get_daily_count(group_id, current_now) >= self.daily_quota:
            return WarmupResult(
                group_id=group_id,
                success=False,
                status="skipped_quota",
                error="daily_quota_exceeded",
            )

        envelope = self.build_warmup_envelope(group_id, config)
        persona = self.get_persona(config)

        try:
            result = await self.service.provider.generate_agent(
                config,
                persona,
                prompt="[保活探测]",
                tools=(),
                history=(),
                images=(),
                request_envelope=envelope,
                max_retries=1,
            )
            usage = dict(result.usage)
            cache_read = int(usage.get("cache_read_tokens") or usage.get("cached_tokens") or 0)
            non_cached = int(usage.get("non_cached_input_tokens") or 0)
            total = int(usage.get("total_tokens") or 0)
            latency = float(usage.get("latency_ms") or 0.0)

            # Record privacy-safe telemetry
            try:
                self.service._write_usage(
                    config,
                    group_id,
                    0,
                    "cache_warmup",
                    mode="shadow",
                    tokens=usage,
                    detail=f"static_hash:{envelope.static_prefix_hash}",
                )
            except Exception:
                pass

            self.record_probe_success(group_id, current_now)

            return WarmupResult(
                group_id=group_id,
                success=True,
                status="warmed",
                cache_read_tokens=cache_read,
                non_cached_input_tokens=non_cached,
                total_tokens=total,
                latency_ms=latency,
            )
        except Exception as exc:
            self.record_probe_failure(group_id, current_now)
            return WarmupResult(
                group_id=group_id,
                success=False,
                status="failed",
                error=type(exc).__name__,
            )

    async def warm_all_eligible(
        self,
        config: TangtangConfig,
        *,
        now: datetime | None = None,
    ) -> tuple[WarmupResult, ...]:
        """Warm all eligible groups concurrently in a bounded fashion."""
        candidates = self.get_candidate_groups(config, now)
        if not candidates:
            return ()
        tasks = [self.warm_group(gid, config, now=now) for gid in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return tuple(results)

    async def run_loop(
        self,
        loader: Any,
        *,
        check_interval_seconds: int = 60,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Run continuous background loop checking and keeping caches warm."""
        interval = max(5, int(check_interval_seconds))
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                config = loader.load() if hasattr(loader, "load") else loader()
                now = datetime.now(DEFAULT_TIMEZONE)
                if is_warmup_window_active(now):
                    await self.warm_all_eligible(config, now=now)
            except asyncio.CancelledError:
                break
            except Exception:
                pass
            try:
                if stop_event is not None:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=interval)
                        break
                    except asyncio.TimeoutError:
                        continue
                else:
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
