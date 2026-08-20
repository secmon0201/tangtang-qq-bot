from __future__ import annotations

import sys
import types

from bot.services.nte_prefix_display import display_command_prefix, patch_upstream_nte_prefix


def test_display_command_prefix_adds_hash_only_when_needed():
    assert display_command_prefix("nte") == "#nte"
    assert display_command_prefix("#nte") == "#nte"
    assert display_command_prefix("") == ""


def test_patch_upstream_nte_prefix_wraps_only_display_helper(monkeypatch):
    original = lambda: "nte"
    prefix_module = types.SimpleNamespace(nte_prefix=original)
    monkeypatch.setitem(sys.modules, "gsuid_core.plugins.NTEUID.NTEUID.nte_config.prefix", prefix_module)
    monkeypatch.setitem(sys.modules, "gsuid_core.plugins.NTEUID.NTEUID.nte_config", types.SimpleNamespace(prefix=prefix_module))
    monkeypatch.setitem(sys.modules, "gsuid_core.plugins.NTEUID.NTEUID", types.ModuleType("nte_root"))
    monkeypatch.setitem(sys.modules, "gsuid_core.plugins.NTEUID", types.ModuleType("nte_plugin"))
    monkeypatch.setitem(sys.modules, "gsuid_core.plugins", types.ModuleType("gsuid_plugins"))
    monkeypatch.setitem(sys.modules, "gsuid_core", types.ModuleType("gsuid_core"))

    assert patch_upstream_nte_prefix() is True
    assert prefix_module.nte_prefix() == "#nte"
    assert patch_upstream_nte_prefix() is True
