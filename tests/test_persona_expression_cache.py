from __future__ import annotations

from types import SimpleNamespace

from bot.services.persona_engine import PersonaEngine


def engine_with_catalog(rows, *, enabled=True):
    engine = object.__new__(PersonaEngine)
    engine.expressions = SimpleNamespace(catalog=lambda _persona: list(rows))
    engine.feature_enabled = lambda _group_id, _feature: enabled
    return engine


def test_stable_expression_catalog_is_deterministic_and_includes_all_public_rows():
    rows = [
        {"id": "expr_b", "name": "B", "group": "g", "visual": "v",
         "emotion": ["e"], "intensity": 0.5, "use": "u", "avoid": "a"},
        {"id": "expr_a", "name": "A", "group": "g", "visual": "v",
         "emotion": ["e"], "intensity": 0.2, "use": "u", "avoid": "a"},
        {"id": "expr_private", "name": "P", "group": "g", "visual": "v",
         "emotion": [], "intensity": 1.0, "use": "u", "avoid": "a",
         "explicit_only": True},
    ]
    engine = engine_with_catalog(rows)
    persona = SimpleNamespace()

    first = engine.stable_expression_catalog_prompt(persona)
    second = engine.stable_expression_catalog_prompt(persona)

    assert first == second
    assert first.index("expr_a") < first.index("expr_b")
    assert "expr_private" not in first


def test_expression_availability_is_dynamic_without_changing_static_catalog():
    rows = [
        {"id": "expr_a", "name": "A", "group": "g", "visual": "v",
         "emotion": [], "intensity": 0.2, "use": "u", "avoid": "a"},
    ]
    persona = SimpleNamespace()
    enabled = engine_with_catalog(rows, enabled=True)
    disabled = engine_with_catalog(rows, enabled=False)
    enabled_context = SimpleNamespace(persona=persona, group_id=1001)
    disabled_context = SimpleNamespace(persona=persona, group_id=1002)

    assert enabled.stable_expression_catalog_prompt(persona) == disabled.stable_expression_catalog_prompt(persona)
    assert "expr_a" in enabled.expression_availability_prompt(enabled_context)
    assert "不可用" in disabled.expression_availability_prompt(disabled_context)


def test_extra_prompt_is_the_stable_and_dynamic_parts_in_original_order(tmp_path):
    rows = []
    engine = engine_with_catalog(rows)
    engine.v2_enabled = lambda _persona: False
    engine.growth = SimpleNamespace(prompt=lambda _persona, _group: "dynamic growth")
    engine.topics = None
    persona = SimpleNamespace(key="tangtang", resource_dir=tmp_path)
    context = SimpleNamespace(persona=persona, group_id=1001, user_id=2001)

    stable = engine.stable_extra_prompt(context)
    dynamic = engine.dynamic_extra_prompt(context, "query")

    assert stable
    assert dynamic == "dynamic growth"
    assert engine.extra_prompt(context, "query") == stable + "\n\n" + dynamic
