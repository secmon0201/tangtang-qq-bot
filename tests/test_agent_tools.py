from __future__ import annotations

import pytest

from bot.services.agent_context import ContextEnvelope
from bot.services.agent_tools import (
    ACTION_TOOL_NAMES,
    BASE_TOOL_SPECS,
    TOOL_REGISTRY,
    parse_action_tool_call,
    tool_schemas,
)


def call(name: str, arguments: str) -> dict[str, str]:
    return {"call_id": "c1", "name": name, "arguments": arguments}


def test_tool_registry_contains_two_search_and_thirty_action_tools():
    assert len(BASE_TOOL_SPECS) == 32
    assert len(TOOL_REGISTRY) == 32
    assert len(ACTION_TOOL_NAMES) == 30
    assert ACTION_TOOL_NAMES == frozenset(
        name for name in TOOL_REGISTRY if name not in {
            "search_zhijiang_knowledge", "search_mingchao_meme_culture"
        }
    )
    assert {spec.delivery for spec in BASE_TOOL_SPECS} == {"model_data", "direct_qq"}
    assert all(spec.effect == "read" for spec in BASE_TOOL_SPECS)
    assert all(spec.explicit_only for spec in BASE_TOOL_SPECS if spec.delivery == "direct_qq")
    schemas = tool_schemas(include_actions=True)
    assert len(schemas) == 32
    assert all(schema["parameters"]["additionalProperties"] is False for schema in schemas)


def test_action_tool_arguments_map_to_existing_feature_contracts():
    assert parse_action_tool_call(call(
        "ranking", '{"period":"周","scope":"cluster"}'
    )).cluster is True
    assert parse_action_tool_call(call(
        "mini_game_dice", '{"scope":"bot"}'
    )).args == "总"
    assert parse_action_tool_call(call("wife_personal", "{}" )).args == ""
    archive = parse_action_tool_call(call(
        "archive_search", '{"target":"mentioned","keyword":"枝江","page":2}'
    ))
    assert archive.parameter("target") == "mentioned"
    assert archive.parameter("keyword") == "枝江"
    role = parse_action_tool_call(call(
        "wuwa_character_rank", '{"scope":"group","page":1,"character":"今汐"}'
    ))
    assert role.args == "群" and role.parameter("character") == "今汐"
    with pytest.raises(ValueError):
        parse_action_tool_call(call("ranking", '{"period":"周","scope":"bot"}'))
    with pytest.raises(ValueError):
        parse_action_tool_call(call("wife_personal", '{"target":"someone"}'))
    with pytest.raises(ValueError):
        parse_action_tool_call(call("unknown", "{}"))
    with pytest.raises(ValueError):
        parse_action_tool_call(call(
            "archive_search", '{"target":"mentioned","keyword":"","page":1}'
        ))
    with pytest.raises(ValueError):
        parse_action_tool_call(call(
            "wuwa_character_rank", '{"scope":"bot","page":0,"character":"今汐"}'
        ))


def test_availability_is_dynamic_and_does_not_change_full_schema_hash():
    schemas = tool_schemas(include_actions=True)
    available = ContextEnvelope.create(
        persona="persona", tools=schemas, dynamic_status="ranking available",
    )
    unavailable = ContextEnvelope.create(
        persona="persona", tools=schemas, dynamic_status="ranking unavailable",
    )
    assert available.tool_schema_hash == unavailable.tool_schema_hash
    assert available.static_prefix_hash == unavailable.static_prefix_hash
