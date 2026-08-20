import asyncio
from types import SimpleNamespace
from time import time

import bot.services.pacing as pacing


def _settings():
    return SimpleNamespace(
        command_prefix="#",
        response_delay_min_seconds=2,
        response_delay_max_seconds=5,
        command_response_delay_min_seconds=1,
        command_response_delay_max_seconds=2,
        onebot_api_min_interval_seconds=0.5,
    )


def test_command_response_uses_the_one_to_two_second_range(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(pacing, "settings", _settings())
    monkeypatch.setattr(pacing, "_current_response", lambda: pacing.OutboundResponse("command"))
    monkeypatch.setattr(pacing.random, "uniform", lambda lower, upper: (lower + upper) / 2)
    monkeypatch.setattr(pacing.asyncio, "sleep", fake_sleep)

    assert asyncio.run(pacing.prepare_outbound_response("send_msg"))
    assert delays == [1.5]


def test_passive_response_uses_the_two_to_five_second_range(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(pacing, "settings", _settings())
    monkeypatch.setattr(pacing, "_current_response", lambda: pacing.OutboundResponse("passive"))
    monkeypatch.setattr(pacing.random, "uniform", lambda lower, upper: (lower + upper) / 2)
    monkeypatch.setattr(pacing.asyncio, "sleep", fake_sleep)

    assert asyncio.run(pacing.prepare_outbound_response("set_msg_emoji_like"))
    assert delays == [3.5]


def test_stale_passive_response_is_dropped_without_waiting(monkeypatch):
    class Event:
        time = int(time()) - pacing.PASSIVE_SEND_MAX_AGE_SECONDS - 1

    async def fail_sleep(_delay):
        raise AssertionError("stale passive responses must not wait or send")

    monkeypatch.setattr(pacing, "settings", _settings())
    monkeypatch.setattr(
        pacing,
        "_current_response",
        lambda: pacing.OutboundResponse("passive", Event()),
    )
    monkeypatch.setattr(pacing.asyncio, "sleep", fail_sleep)

    assert not asyncio.run(pacing.prepare_outbound_response("send_group_msg"))


def test_scheduled_notifications_skip_humanized_delay(monkeypatch):
    async def fail_sleep(_delay):
        raise AssertionError("scheduled notifications only use OneBot API pacing")

    monkeypatch.setattr(pacing, "_current_response", lambda: None)
    monkeypatch.setattr(pacing.asyncio, "sleep", fail_sleep)

    assert asyncio.run(pacing.prepare_outbound_response("send_group_msg"))
