from __future__ import annotations

import json
import os
from dataclasses import dataclass

from bot.config import Settings


@dataclass(frozen=True, slots=True)
class OfficialQQRuntime:
    """Non-secret OpenAPI runtime facts suitable for diagnostics."""

    app_id: str
    sandbox: bool
    port: int


def official_runtime(settings: Settings) -> OfficialQQRuntime:
    if settings.transport != "qq_openapi":
        raise ValueError("official QQ runtime is disabled")
    if not settings.official_app_id or not settings.official_token or not settings.official_app_secret:
        raise ValueError("official QQ credentials are incomplete")
    return OfficialQQRuntime(
        app_id=settings.official_app_id,
        sandbox=settings.official_sandbox,
        port=settings.official_port,
    )


def configure_official_environment(settings: Settings) -> OfficialQQRuntime:
    """Configure the official adapter without logging or persisting credentials."""

    runtime = official_runtime(settings)
    os.environ["DRIVER"] = "~fastapi+~httpx+~websockets"
    os.environ["PORT"] = str(runtime.port)
    os.environ["QQ_IS_SANDBOX"] = "true" if runtime.sandbox else "false"
    os.environ["QQ_BOTS"] = json.dumps(
        [
            {
                "id": runtime.app_id,
                "token": settings.official_token,
                "secret": settings.official_app_secret,
                "intent": {
                    "guilds": False,
                    "guild_members": False,
                    "guild_messages": False,
                    "guild_message_reactions": False,
                    "direct_message": False,
                    "c2c_group_at_messages": True,
                    "interaction": False,
                    "message_audit": False,
                    "at_messages": False,
                },
                "use_websocket": True,
            }
        ],
        ensure_ascii=False,
    )
    return runtime
