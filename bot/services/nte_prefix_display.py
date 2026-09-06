"""Project-side display compatibility for the upstream NTEUID command prefix."""

from __future__ import annotations

import sys


def display_command_prefix(prefix: str) -> str:
    """Return a copyable command prefix for user-facing NTEUID messages."""
    value = str(prefix or "").strip()
    if not value or value.startswith("#"):
        return value
    return f"#{value}"


def patch_upstream_nte_prefix() -> bool:
    """Make upstream NTEUID feedback show the project's required hash prefix.

    NTEUID still matches its native ``nte`` force prefix internally. Only the
    helper used for feedback text, help registration, and buttons is wrapped.
    """
    # GsUID Core is an independent process. Importing its package here can run
    # configuration writers concurrently with the live Core process on Windows.
    # Only patch a module another same-process integration already loaded.
    prefix_module = sys.modules.get("gsuid_core.plugins.NTEUID.NTEUID.nte_config.prefix")
    if prefix_module is None:
        return False

    original = getattr(prefix_module, "nte_prefix", None)
    if not callable(original):
        return False
    if getattr(original, "_project_hash_display", False):
        return True

    def prefixed() -> str:
        return display_command_prefix(original())

    prefixed._project_hash_display = True  # type: ignore[attr-defined]
    prefix_module.nte_prefix = prefixed
    return True


__all__ = ["display_command_prefix", "patch_upstream_nte_prefix"]
