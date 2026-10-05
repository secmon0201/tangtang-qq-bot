"""Tests for AgentCacheWarmer (Phase 2 / Q10)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from bot.services.tangtang_cache_warmer import (
    DEFAULT_TIMEZONE,
    AgentCacheWarmer,
    WarmupResult,
    is_warmup_window_active,
)
from bot.services.tangtang_chat import AgentResult, TangtangConfig, TangtangService


def _config(**kwargs: Any) -> TangtangConfig:
    values = {
        "TANGTANG_ENABLED": "true",
        "TANGTANG_GROUP_IDS": "1001,1002",
        "TANGTANG_CONTEXT_LAYOUT": "v2",
        "TANGTANG_CACHE_COHORT_MODE": "on",
        "TANGTANG_API_URL": "https://example.invalid/v1",
        "TANGTANG_API_KEY": "test-key",
        "TANGTANG_MODEL": "gpt-5.6-luna",
    }
    values.update(kwargs)
    return TangtangConfig.from_values(values, (1001, 1002))


def test_warmup_window_active():
    # Daytime: 14:00 -> active
    dt_day = datetime(2026, 10, 3, 14, 0, tzinfo=DEFAULT_TIMEZONE)
    assert is_warmup_window_active(dt_day) is True

    # Nighttime: 02:00 -> inactive
    dt_night = datetime(2026, 10, 3, 2, 0, tzinfo=DEFAULT_TIMEZONE)
    assert is_warmup_window_active(dt_night) is False

    # Exact boundaries: 09:00 -> active, 23:30 -> active, 23:31 -> inactive
    assert is_warmup_window_active(datetime(2026, 10, 3, 9, 0, tzinfo=DEFAULT_TIMEZONE)) is True
    assert is_warmup_window_active(datetime(2026, 10, 3, 23, 30, tzinfo=DEFAULT_TIMEZONE)) is True
    assert is_warmup_window_active(datetime(2026, 10, 3, 23, 31, tzinfo=DEFAULT_TIMEZONE)) is False


def test_candidate_groups_filtering():
    service = TangtangService(db=None)
    warmer = AgentCacheWarmer(service, interval_seconds=300)
    config = _config()

    # Day time: candidates returned
    now_day = datetime(2026, 10, 3, 15, 0, tzinfo=DEFAULT_TIMEZONE)
    candidates = warmer.get_candidate_groups(config, now=now_day)
    assert candidates == (1001, 1002)

    # After warming 1001, only 1002 remains within interval
    warmer._last_warmed_at[1001] = now_day
    candidates = warmer.get_candidate_groups(config, now=now_day)
    assert candidates == (1002,)

    # Night time: empty
    now_night = datetime(2026, 10, 3, 3, 0, tzinfo=DEFAULT_TIMEZONE)
    assert warmer.get_candidate_groups(config, now=now_night) == ()


def test_build_warmup_envelope():
    service = TangtangService(db=None)
    warmer = AgentCacheWarmer(service)
    config = _config()

    envelope = warmer.build_warmup_envelope(1001, config)
    assert envelope.cache_affinity_key == "group-1001-spine"
    assert "[保活探测]" in envelope.current_text
    assert envelope.static_prefix_hash


def test_warm_group_success_and_usage():
    async def _test():
        service = TangtangService(db=None)
        service.provider = MagicMock()
        service._write_usage = MagicMock()

        service.provider.generate_agent = AsyncMock(
            return_value=AgentResult(
                text="pong",
                tool_calls=(),
                usage={
                    "prompt_tokens": 1200,
                    "cached_tokens": 1000,
                    "cache_read_tokens": 1000,
                    "non_cached_input_tokens": 200,
                    "total_tokens": 1201,
                    "latency_ms": 250.0,
                },
            )
        )

        warmer = AgentCacheWarmer(service)
        config = _config()
        now = datetime(2026, 10, 3, 12, 0, tzinfo=DEFAULT_TIMEZONE)

        res = await warmer.warm_group(1001, config, now=now)
        assert res.success is True
        assert res.status == "warmed"
        assert res.cache_read_tokens == 1000
        assert res.non_cached_input_tokens == 200
        assert warmer._last_warmed_at[1001] == now
        assert warmer.get_daily_count(1001, now) == 1
        service._write_usage.assert_called_once()

    asyncio.run(_test())


def test_warm_group_failure_handled_and_circuit_pause():
    async def _test():
        service = TangtangService(db=None)
        service.provider = MagicMock()
        service.provider.generate_agent = AsyncMock(side_effect=RuntimeError("timeout"))

        warmer = AgentCacheWarmer(service, failure_threshold=2)
        config = _config()
        now = datetime(2026, 10, 3, 12, 0, tzinfo=DEFAULT_TIMEZONE)

        # 1st failure
        res1 = await warmer.warm_group(1001, config, now=now)
        assert res1.success is False
        assert res1.status == "failed"
        assert warmer.is_group_circuit_open(1001, now) is False

        # 2nd failure -> circuit open
        res2 = await warmer.warm_group(1001, config, now=now)
        assert res2.success is False
        assert res2.status == "failed"
        assert warmer.is_group_circuit_open(1001, now) is True

        # 3rd attempt skipped by circuit open
        res3 = await warmer.warm_group(1001, config, now=now)
        assert res3.success is False
        assert res3.status == "skipped_circuit"
        assert res3.error == "circuit_open"

        # After pause duration (30 mins), circuit recovers
        later = now + timedelta(minutes=31)
        assert warmer.is_group_circuit_open(1001, later) is False

    asyncio.run(_test())


def test_daily_quota_limit():
    async def _test():
        service = TangtangService(db=None)
        service.provider = MagicMock()
        service.provider.generate_agent = AsyncMock(
            return_value=AgentResult(text="pong", tool_calls=(), usage={})
        )

        warmer = AgentCacheWarmer(service, daily_quota=2)
        config = _config()
        now = datetime(2026, 10, 3, 12, 0, tzinfo=DEFAULT_TIMEZONE)

        res1 = await warmer.warm_group(1001, config, now=now)
        assert res1.status == "warmed"
        res2 = await warmer.warm_group(1001, config, now=now)
        assert res2.status == "warmed"

        # Exceeds quota (2)
        res3 = await warmer.warm_group(1001, config, now=now)
        assert res3.status == "skipped_quota"
        assert res3.error == "daily_quota_exceeded"

        # Next day: quota resets
        tomorrow = now + timedelta(days=1)
        res_next_day = await warmer.warm_group(1001, config, now=tomorrow)
        assert res_next_day.status == "warmed"

    asyncio.run(_test())


def test_warmer_run_loop_lifecycle():
    async def _test():
        service = TangtangService(db=None)
        warmer = AgentCacheWarmer(service)
        warmer.warm_all_eligible = AsyncMock(return_value=())
        loader = MagicMock()
        loader.load = MagicMock(return_value=_config())
        stop_event = asyncio.Event()

        task = asyncio.create_task(
            warmer.run_loop(loader, check_interval_seconds=1, stop_event=stop_event)
        )
        await asyncio.sleep(0.05)
        stop_event.set()
        await task
        assert warmer.warm_all_eligible.called

    asyncio.run(_test())
