from __future__ import annotations

import asyncio
import importlib
import inspect
from types import ModuleType
from typing import Any

from bot.integrations.gsuid_core_compat import GsuidCompatibilityError


_PATCH_MARKER = "__qqbot_event_path_isolation__"
_SEND_PATCH_MARKER = "__qqbot_send_target_resolution__"
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

    try:
        client_module = importlib.import_module(f"{connector_module.__name__}.client")
    except ImportError as exc:
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; client module is unavailable."
        ) from exc
    current_pick_bots = getattr(client_module, "_pick_bots", None)
    if not callable(current_pick_bots):
        raise GsuidCompatibilityError(
            "GenshinUID connector API changed; client._pick_bots is unavailable."
        )

    if not getattr(current_pick_bots, _SEND_PATCH_MARKER, False):
        from nonebot import get_bots

        identity_module = importlib.import_module(
            f"{connector_module.__name__}.identity"
        )
        resolve_bot = getattr(identity_module, "resolve_bot", None)
        if not callable(resolve_bot):
            raise GsuidCompatibilityError(
                "GenshinUID connector API changed; identity.resolve_bot is unavailable."
            )

        def pick_bots(msg: Any) -> list[Any]:
            bots = get_bots()
            self_id = str(getattr(msg, "bot_self_id", "") or "")
            exact = bots.get(self_id) if self_id else None
            if exact is not None:
                return [exact]
            found = resolve_bot(
                str(getattr(msg, "bot_id", "") or ""),
                self_id,
            )
            return [found] if found is not None else []

        setattr(pick_bots, _SEND_PATCH_MARKER, True)
        client_module._pick_bots = pick_bots

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
