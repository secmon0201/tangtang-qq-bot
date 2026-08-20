"""Shared Tangtang runtime objects without NoneBot matcher registration."""

from __future__ import annotations

from bot.services.tangtang_chat import TangtangConfigLoader


config_loader = TangtangConfigLoader()


__all__ = ["config_loader"]
