"""Backward-compatible import names for the QQ platform boundary."""

from bot.services.qq_platform import QQPlatform as OneBotGateway
from bot.services.qq_platform import QQPlatformError as GatewayError

__all__ = ["GatewayError", "OneBotGateway"]
