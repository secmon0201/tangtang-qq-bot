import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from bot.application.chat_continuation import continuation_config, refresh_continuation_quotas
from bot.services.continuation_policy import ContinuationStore, continuation_daily_limit
from bot.services.qq_platform import QQPlatform


NOW = datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


@pytest.mark.parametrize("members,limit", [(0, 20), (80, 20), (100, 20), (101, 40),
                                         (200, 40), (250, 60), (300, 60), (301, 80)])
def test_population_rounds_up_to_hundreds(tmp_path, members, limit):
    store = ContinuationStore(tmp_path / "quota.db")
    assert continuation_daily_limit(members) == limit
    assert store.begin_quota_refresh(NOW)
    store.finish_quota_refresh(NOW, {1001: members})
    assert store.daily_limit(1001, NOW) == limit
    for n in range(limit):
        assert store.claim(1001, str(n), NOW)
    assert not store.claim(1001, "over", NOW)


def test_refresh_is_once_per_day_across_concurrency_restarts_and_population_changes(tmp_path, monkeypatch):
    path = tmp_path / "quota.db"
    now = [NOW]
    members = [250]
    calls = []

    async def group_list(_):
        calls.append(now[0])
        await asyncio.sleep(0)
        return [{"group_id": 1001, "member_count": members[0]},
                {"group_id": 1002, "member_count": 80}]

    monkeypatch.setattr(QQPlatform, "group_list", group_list)

    async def refresh():
        await refresh_continuation_quotas(object(), store=ContinuationStore(path), clock=lambda: now[0])

    async def scenario():
        await asyncio.gather(refresh(), refresh())
        members[0] = 80
        await refresh()
        store = ContinuationStore(path)
        assert store.daily_limit(1001, now[0]) == 60
        assert store.daily_limit(1002, now[0]) == 20
        assert len(calls) == 1
        assert store.claim(1001, "before-midnight", now[0])
        now[0] = NOW + 12 * 3600  # Midnight in Shanghai, not UTC.
        await refresh()
        assert len(calls) == 2
        assert store.daily_limit(1001, now[0]) == 20
        for n in range(20):
            assert store.claim(1001, f"new-day:{n}", now[0])
        assert not store.claim(1001, "over", now[0])

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "timeout", "missing_count"])
def test_refresh_failure_keeps_saved_limits_without_retrying(tmp_path, monkeypatch, failure):
    store = ContinuationStore(tmp_path / "quota.db")
    store.begin_quota_refresh(NOW - 86400)
    store.finish_quota_refresh(NOW - 86400, {1001: 250})
    calls = []

    async def group_list(_):
        calls.append(1)
        if failure == "error":
            raise RuntimeError("offline")
        if failure == "timeout":
            raise TimeoutError()
        return [{"group_id": 1001}, {"group_id": 1002, "member_count": "invalid"}]

    monkeypatch.setattr(QQPlatform, "group_list", group_list)

    async def scenario():
        for _ in range(3):
            await refresh_continuation_quotas(object(), store=store, clock=lambda: NOW)

    asyncio.run(scenario())
    assert len(calls) == 1
    assert store.daily_limit(1001, NOW) == 60
    assert store.daily_limit(1002, NOW) == 20


def test_admission_only_reads_snapshot_and_preserves_existing_usage(tmp_path):
    store = ContinuationStore(tmp_path / "quota.db")
    for n in range(10):
        assert store.claim(1001, f"old:{n}", NOW)
        store.outcome(f"old:{n}", "silent" if n % 2 else "cancelled_or_failed")
    store.begin_quota_refresh(NOW)
    store.finish_quota_refresh(NOW, {1001: 250})
    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(lambda n: store.claim(1001, f"new:{n}", NOW), range(70)))
    assert sum(accepted) == 50
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM continuation_attempts").fetchone()[0] == 60
        assert conn.execute("SELECT count(*) FROM continuation_quota_refreshes").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM continuation_group_quotas").fetchone()[0] == 1


def test_legacy_caps_are_ignored_and_window_defaults_match_operator_settings():
    options = {"continuation_group_daily_limit": 1, "continuation_global_daily_limit": 1}
    config = continuation_config(SimpleNamespace(option=lambda k, d: options.get(k, d)))
    assert (config.idle_seconds, config.hard_seconds, config.max_attempts) == (30, 300, 4)
    assert not hasattr(config, "global_daily")
    assert not hasattr(config, "group_daily")


def test_interrupted_refresh_is_not_repeated_after_restart(tmp_path):
    path = tmp_path / "quota.db"
    assert ContinuationStore(path).begin_quota_refresh(NOW)
    restarted = ContinuationStore(path)
    assert not restarted.begin_quota_refresh(NOW)
    assert restarted.daily_limit(1001, NOW) == 20
    assert restarted.begin_quota_refresh(NOW + 86400)


def test_disconnected_midnight_leaves_catchup_available(tmp_path, monkeypatch):
    from bot.plugins import tangtang_chat as plugin

    monkeypatch.setattr(plugin, "get_bots", lambda: {})

    async def unexpected_refresh(*args):
        raise AssertionError("offline midnight must not consume the refresh")

    monkeypatch.setattr(plugin, "refresh_continuation_quotas", unexpected_refresh)
    asyncio.run(plugin.refresh_daily_continuation_quotas())


def test_scheduler_uses_midnight_and_connection_only_schedules_background_work(monkeypatch):
    from bot.plugins import tangtang_chat as plugin

    jobs = []
    monkeypatch.setattr(plugin, "_continuation_quota_scheduler", SimpleNamespace(
        add_job=lambda *args, **kwargs: jobs.append((args, kwargs)), start=lambda: None))

    async def scenario():
        await plugin.start_proactive_timer()
        try:
            await plugin.schedule_continuation_quota_catchup(object())
        finally:
            plugin._proactive_task.cancel()
            await asyncio.gather(plugin._proactive_task, return_exceptions=True)

    asyncio.run(scenario())
    assert jobs[0][0][1] == "cron"
    assert jobs[0][1]["hour"] == jobs[0][1]["minute"] == 0
    assert jobs[1][0][1] == "date"
    assert all(job[1]["max_instances"] == 1 for job in jobs)
