"""Project-side display compatibility for the upstream NTEUID command prefix."""

from __future__ import annotations


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
    try:
        from gsuid_core.plugins.NTEUID.NTEUID.nte_config import prefix as prefix_module
    except (ImportError, ModuleNotFoundError):
        return False

    original = prefix_module.nte_prefix
    if getattr(original, "_project_hash_display", False):
        return True

    def prefixed() -> str:
        return display_command_prefix(original())

    prefixed._project_hash_display = True  # type: ignore[attr-defined]
    prefix_module.nte_prefix = prefixed
    return True


__all__ = ["display_command_prefix", "patch_upstream_nte_prefix"]
