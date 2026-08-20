from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.integrations.gsuid_core_compat import (
    GsuidCompatibilityError,
    install_plugin_loading_compatibility,
    windows_atomic_save,
)


def test_windows_atomic_save_replaces_destination_and_cleans_temporary_files(tmp_path):
    destination = tmp_path / "config.json"
    destination.write_bytes(b"old")

    with windows_atomic_save(destination, text_mode=False, overwrite=True) as handle:
        handle.write(b"new")

    assert destination.read_bytes() == b"new"
    assert list(tmp_path.glob(".config.json.qqbot-*.tmp")) == []


def test_windows_atomic_save_keeps_destination_when_write_fails(tmp_path):
    destination = tmp_path / "config.json"
    destination.write_bytes(b"old")

    with pytest.raises(RuntimeError):
        with windows_atomic_save(destination, text_mode=False, overwrite=True) as handle:
            handle.write(b"partial")
            raise RuntimeError("stop")

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob(".config.json.qqbot-*.tmp")) == []


def test_plugin_loading_adapter_skips_only_disabled_external_plugins(tmp_path):
    plugin_root = tmp_path / "plugins"
    plugin_root.mkdir()
    enabled = plugin_root / "NTEUID"
    disabled = plugin_root / "GenshinUID"
    builtin = tmp_path / "builtin" / "help"
    configs = {
        "NTEUID": {"enabled": True},
        "GenshinUID": {"enabled": False},
    }
    server = SimpleNamespace(
        PLUGIN_PATH=plugin_root,
        plugin_config_store=SimpleNamespace(get=lambda name: configs.get(name, {})),
        should_load_plugin=lambda _plugin, _dev_mode: True,
        logger=SimpleNamespace(info=lambda _message: None),
    )

    install_plugin_loading_compatibility(server)

    assert server.should_load_plugin(enabled, False)
    assert not server.should_load_plugin(disabled, False)
    assert server.should_load_plugin(builtin, False)


def test_plugin_loading_adapter_fails_loudly_when_upstream_api_changes():
    with pytest.raises(GsuidCompatibilityError, match="plugin API changed"):
        install_plugin_loading_compatibility(SimpleNamespace())
