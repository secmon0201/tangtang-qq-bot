"""Protect live runtime files before pytest imports plugin composition modules."""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory


SOURCE_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIRECTORY = TemporaryDirectory(prefix="qq-bot-pytest-", ignore_cleanup_errors=True)
RUNTIME_ROOT = Path(_RUNTIME_DIRECTORY.name)
_MUTABLE_ROOTS = {"data", "logs", "downloads", "backups", ".env"}


class _IsolatedRuntimeRoot(type(Path())):
    def __truediv__(self, value):
        relative = Path(value)
        if (self == SOURCE_ROOT and relative.parts
                and relative.parts[0].casefold() in _MUTABLE_ROOTS):
            return RUNTIME_ROOT / relative
        return super().__truediv__(value)

    def joinpath(self, *values):
        result = self
        for value in values:
            result = result / value
        return result


def install_runtime_isolation() -> None:
    # Static resources and read-only upstream fixtures remain in the checkout.
    # Explicit environment paths must also override private dotenv defaults.
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["QQ_BOT_ROOT"] = str(SOURCE_ROOT)
    os.environ["BOT_DB_PATH"] = str(RUNTIME_ROOT / "data" / "bot.db")
    os.environ["REPORT_DIR"] = str(RUNTIME_ROOT / "data" / "reports")
    os.environ["AVATAR_CACHE_DIR"] = str(RUNTIME_ROOT / "data" / "avatar_cache")
    from bot import config
    config.ROOT = _IsolatedRuntimeRoot(SOURCE_ROOT)
