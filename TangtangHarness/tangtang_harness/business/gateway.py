"""Backward-compatible import names for the QQ platform boundary."""

from tangtang_harness.business.qq_platform import QQPlatform as OneBotGateway
from tangtang_harness.business.qq_platform import QQPlatformError as GatewayError

__all__ = ["GatewayError", "OneBotGateway"]
