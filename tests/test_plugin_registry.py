from __future__ import annotations

from pathlib import Path

from bot.application.plugin_registry import PLUGIN_SPECS, plugin_specs_for


ROOT = Path(__file__).resolve().parents[1]


def test_every_plugin_file_is_registered_exactly_once():
    plugin_modules = {
        "bot.plugins." + path.stem
        for path in (ROOT / "bot" / "plugins").glob("*.py")
        if path.name != "__init__.py"
    }
    registered_modules = [spec.module for spec in PLUGIN_SPECS]

    assert len(registered_modules) == len(set(registered_modules))
    assert set(registered_modules) == plugin_modules


def test_registry_has_stable_chinese_labels_and_existing_sources():
    for spec in PLUGIN_SPECS:
        assert spec.key
        assert spec.label_zh
        assert spec.category_zh
        assert any("\u4e00" <= character <= "\u9fff" for character in spec.label_zh)
        source = ROOT / (spec.module.replace(".", "/") + ".py")
        assert source.is_file()


def test_transport_and_feature_flags_are_resolved_without_importing_plugins():
    enabled = plugin_specs_for("onebot", stats_realtime_enabled=True)
    disabled = plugin_specs_for("onebot", stats_realtime_enabled=False)
    official = plugin_specs_for("qq_openapi", stats_realtime_enabled=True)

    assert {spec.key for spec in enabled} >= {
        "runtime_maintenance",
        "tangtang_proactive",
        "tangtang_model_switch",
        "stats",
        "a_coast_archive",
    }
    assert {spec.key for spec in disabled}.isdisjoint({"stats", "a_coast_archive"})
    assert [spec.key for spec in official] == ["official_qq"]


def test_declared_plugin_ordering_dependencies_are_satisfied():
    for transport in ("onebot", "qq_openapi"):
        specs = plugin_specs_for(transport, stats_realtime_enabled=True)
        positions = {spec.key: index for index, spec in enumerate(specs)}
        for spec in specs:
            for dependency in spec.after:
                if dependency in positions:
                    assert positions[dependency] < positions[spec.key]
