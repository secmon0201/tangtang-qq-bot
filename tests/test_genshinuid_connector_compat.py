from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.integrations.genshinuid_connector_compat import (
    install_genshinuid_connector_compatibility,
)
from bot.integrations.gsuid_core_compat import GsuidCompatibilityError


ROOT = Path(__file__).resolve().parents[1]


def _connector(*, client=None):
    async def ensure_client():
        await asyncio.Event().wait()

    async def connect():
        await asyncio.Event().wait()

    async def repeat_connect():
        await connect()

    return SimpleNamespace(
        _ensure_client=ensure_client,
        connect=connect,
        connect_lock=asyncio.Lock(),
        repeat_connect=repeat_connect,
        gsclient=client,
    )


def test_offline_event_path_returns_immediately_without_reconnecting():
    connector = _connector()
    original_repeat_connect = connector.repeat_connect
    install_genshinuid_connector_compatibility(connector)

    async def call_many():
        return await asyncio.wait_for(
            asyncio.gather(*(connector._ensure_client() for _ in range(500))),
            timeout=0.5,
        )

    assert asyncio.run(call_many()) == [None] * 500
    assert not hasattr(connector, "_qqbot_background_connect_task")
    assert connector.repeat_connect is original_repeat_connect


def test_background_connect_is_immediate_and_single_flight():
    async def exercise():
        attempts = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def connect():
            nonlocal attempts
            attempts += 1
            started.set()
            await release.wait()

        connector = _connector()
        connector.connect = connect
        install_genshinuid_connector_compatibility(connector)

        await asyncio.wait_for(
            asyncio.gather(*(connector.connect() for _ in range(500))),
            timeout=0.5,
        )
        await asyncio.wait_for(started.wait(), timeout=0.5)
        assert attempts == 1

        release.set()
        await connector._qqbot_background_connect_task

    asyncio.run(exercise())


def test_online_event_path_returns_current_client():
    client = object()
    connector = _connector(client=client)
    install_genshinuid_connector_compatibility(connector)

    assert asyncio.run(connector._ensure_client()) is client


def test_adapter_is_idempotent():
    connector = _connector()
    install_genshinuid_connector_compatibility(connector)
    patched_ensure = connector._ensure_client
    patched_connect = connector.connect

    install_genshinuid_connector_compatibility(connector)

    assert connector._ensure_client is patched_ensure
    assert connector.connect is patched_connect


@pytest.mark.parametrize(
    "missing",
    ["_ensure_client", "connect", "connect_lock", "repeat_connect", "gsclient"],
)
def test_adapter_fails_loudly_when_upstream_api_changes(missing):
    connector = _connector()
    delattr(connector, missing)

    with pytest.raises(GsuidCompatibilityError, match=missing):
        install_genshinuid_connector_compatibility(connector)


def test_adapter_is_installed_after_connector_load_and_before_run():
    source = (ROOT / "bot" / "__main__.py").read_text(encoding="utf-8")

    connector_load = source.index('nonebot.load_plugin("GenshinUID")')
    adapter_install = source.index("install_genshinuid_connector_compatibility()")
    repeat_enabled = source.index('os.environ.setdefault("gsuid_core_repeat", "true")')
    initialized = source.index("nonebot.init()")
    run = source.index("nonebot.run()")

    assert repeat_enabled < initialized
    assert connector_load < adapter_install < run
