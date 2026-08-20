from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, BinaryIO, TextIO


class GsuidCompatibilityError(RuntimeError):
    """Raised when an upstream change invalidates the local runtime adapter."""


@contextmanager
def windows_atomic_save(
    dest_path: str | os.PathLike[str],
    *,
    text_mode: bool = False,
    overwrite: bool = True,
    file_perms: int | None = None,
    **_options: Any,
) -> Iterator[BinaryIO | TextIO]:
    """Write beside the destination and replace it using Windows-safe semantics."""
    destination = Path(dest_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    mode = "w" if text_mode else "wb"
    encoding = "utf-8" if text_mode else None
    handle = tempfile.NamedTemporaryFile(
        mode=mode,
        encoding=encoding,
        delete=False,
        dir=destination.parent,
        prefix=f".{destination.name}.qqbot-",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        if file_perms is not None:
            destination.chmod(file_perms)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def install_preimport_compatibility(fileutils: ModuleType | None = None) -> None:
    """Patch the shared writer before GsUID Core imports it into its modules."""
    if fileutils is None:
        try:
            from boltons import fileutils as imported_fileutils
        except ImportError as exc:
            raise GsuidCompatibilityError(
                "GsUID Core dependency 'boltons' is unavailable; reinstall Core dependencies."
            ) from exc
        fileutils = imported_fileutils
    if not hasattr(fileutils, "atomic_save"):
        raise GsuidCompatibilityError(
            "GsUID Core's boltons dependency no longer exposes atomic_save."
        )
    fileutils.atomic_save = windows_atomic_save


def install_plugin_loading_compatibility(server_module: ModuleType) -> None:
    """Make Core honor each external plugin's enabled flag before import."""
    required = ("should_load_plugin", "PLUGIN_PATH", "plugin_config_store")
    missing = [name for name in required if not hasattr(server_module, name)]
    if missing:
        raise GsuidCompatibilityError(
            "GsUID Core plugin API changed; missing: " + ", ".join(missing)
        )

    original = server_module.should_load_plugin
    if getattr(original, "__qqbot_compat__", False):
        return

    def should_load_plugin(plugin: Path, dev_mode: bool) -> bool:
        if not original(plugin, dev_mode):
            return False
        if Path(plugin).parent != Path(server_module.PLUGIN_PATH):
            return True
        config = server_module.plugin_config_store.get(Path(plugin).name)
        if not config or "enabled" not in config:
            return True
        enabled = bool(config["enabled"])
        if not enabled and hasattr(server_module, "logger"):
            server_module.logger.info(f"Skip disabled plugin: {Path(plugin).name}")
        return enabled

    should_load_plugin.__qqbot_compat__ = True
    server_module.should_load_plugin = should_load_plugin


def install_gsuid_core_compatibility() -> None:
    """Install all project-owned adapters before Core starts loading plugins."""
    install_preimport_compatibility()
    try:
        from gsuid_core import server
    except ImportError as exc:
        raise GsuidCompatibilityError(
            "GsUID Core could not be imported; run scripts/install_gsuid.ps1 first."
        ) from exc
    install_plugin_loading_compatibility(server)
