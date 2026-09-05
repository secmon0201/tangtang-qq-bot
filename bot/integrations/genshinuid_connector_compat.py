from __future__ import annotations

import asyncio
import importlib
import inspect
from types import ModuleType
from typing import Any

from bot.integrations.gsuid_core_compat import GsuidCompatibilityError


_PATCH_MARKER = "__qqbot_event_path_isolation__"
_CONNECT_TASK = "_qqbot_background_connect_task"


def install_genshinuid_connector_compatibility(
    connector_module: ModuleType | None = None,
) -> None:
    """Keep Core reconnect work out of every incoming-message handler."""
    if connector_module is None:
        try:
            connector_module = importlib.import_module("GenshinUID")
        except ImportError as exc:
            raise GsuidCompatibilityError(
                "GenshinUID connector could not be imported; reinstall GsUID dependencies."
            ) from exc

    required = ("_ensure_client", "connect", "connect_lock", "repeat_connect", "gsclient")
    missing = [name for name in required if not hasattr(connector_module, name)]
    if missing:
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; missing: " + ", ".join(missing)
        )

    current_ensure = connector_module._ensure_client
    if getattr(current_ensure, _PATCH_MARKER, False):
        return
    if not inspect.iscoroutinefunction(current_ensure):
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; _ensure_client is not async."
        )
    original_connect = connector_module.connect
    if not inspect.iscoroutinefunction(original_connect):
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; connect is not async."
        )
    if not inspect.iscoroutinefunction(connector_module.repeat_connect):
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; repeat_connect is not async."
        )

    async def get_connected_client() -> Any | None:
        # The scheduler owns reconnect and health checks. An event must only
        # observe current state so one offline dependency cannot delay others.
        return connector_module.gsclient

    async def request_background_connection() -> None:
        if connector_module.gsclient is not None:
            return
        pending = getattr(connector_module, _CONNECT_TASK, None)
        if pending is not None and not pending.done():
            return

        task = asyncio.create_task(
            original_connect(),
            name="qqbot-gsuid-background-connect",
        )
        setattr(connector_module, _CONNECT_TASK, task)

        def consume_result(completed: asyncio.Task[Any]) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger = getattr(connector_module, "logger", None)
                if logger is not None:
                    logger.exception(exc)

        task.add_done_callback(consume_result)

    setattr(get_connected_client, _PATCH_MARKER, True)
    setattr(request_background_connection, _PATCH_MARKER, True)
    connector_module._ensure_client = get_connected_client
    connector_module.connect = request_background_connection
