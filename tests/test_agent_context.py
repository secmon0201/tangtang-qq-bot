from __future__ import annotations

from dataclasses import replace

from bot.services.agent_context import AGENT_REPLY_INSTRUCTIONS, ContextEnvelope
from bot.services.persona_contracts import INSTRUCTION, TurnSnapshot
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider


TOOLS = ({
    "type": "function",
    "name": "lookup",
    "description": "stable lookup",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
},)


def test_dynamic_state_does_not_change_static_hash():
    first = ContextEnvelope.create(
        persona="persona-v1", dynamic_status="group one; available", current_input="hello",
        tools=TOOLS,
    )
    second = ContextEnvelope.create(
        persona="persona-v1", dynamic_status="group two; unavailable", current_input="different",
        tools=TOOLS,
    )
    assert first.static_prefix_hash == second.static_prefix_hash
    assert first.tool_schema_hash == second.tool_schema_hash
    assert first.shadow_fingerprint() != second.shadow_fingerprint()


def test_persona_or_schema_change_invalidates_static_hash():
    baseline = ContextEnvelope.create(persona="persona-v1", tools=TOOLS)
    persona_changed = ContextEnvelope.create(persona="persona-v2", tools=TOOLS)
    schema_changed = ContextEnvelope.create(
        persona="persona-v1",
        tools=({**TOOLS[0], "description": "new contract"},),
    )
    assert baseline.static_prefix_hash != persona_changed.static_prefix_hash
    assert baseline.static_prefix_hash != schema_changed.static_prefix_hash
    assert baseline.tool_schema_hash == persona_changed.tool_schema_hash
    assert baseline.tool_schema_hash != schema_changed.tool_schema_hash


def test_next_turn_is_append_only_before_new_current_input():
    first = ContextEnvelope.create(
        persona="persona", dynamic_status="status-1", current_input="question-1", tools=TOOLS,
    )
    first_items = first.canonical_semantic_items()
    second = ContextEnvelope.create(
        persona="persona",
        conversation_items=(
            first_items[-1],
            {"type": "message", "role": "assistant", "content": "answer-1"},
        ),
        dynamic_status="status-2",
        current_input="question-2",
        tools=TOOLS,
    )
    second_items = second.canonical_semantic_items()
    assert second_items[: len(first_items)] == first_items
    assert second_items[len(first_items)]["role"] == "assistant"
    assert second_items[-1]["content"].endswith("question-2")


def test_layer_sizes_contain_counts_not_content():
    envelope = ContextEnvelope.create(
        persona="persona", compacted_snapshot="facts", dynamic_status="status",
        current_input="message", tools=TOOLS,
    )
    sizes = envelope.layer_sizes()
    assert sizes["static_prefix_chars"] > len("persona")
    assert sizes["snapshot_chars"] == 5
    assert sizes["dynamic_status_chars"] == 6
    assert sizes["current_input_chars"] == 7
    assert all(isinstance(value, int) for value in sizes.values())


def test_stable_prefix_contains_the_output_protocol():
    envelope = ContextEnvelope.create(persona="persona", tools=TOOLS)
    assert AGENT_REPLY_INSTRUCTIONS in envelope.static_text
    assert "第一行必须是 [接话] 或 [沉默]" in envelope.static_text


def test_layered_payload_places_tools_before_the_changing_conversation():
    envelope = ContextEnvelope.create(persona="persona", current_input="current", tools=TOOLS)
    base = TangtangConfig.disabled("test")
    for style, builder, conversation_key in (
        ("responses", TangtangProvider._responses_payload, "input"),
        ("chat_completions", TangtangProvider._chat_payload, "messages"),
    ):
        config = replace(base, api_style=style, model="model", max_output_tokens=32)
        payload = builder(
            config,
            envelope.static_text,
            envelope.current_text,
            tools=TOOLS,
            envelope=envelope,
        )
        keys = tuple(payload)
        assert keys.index("tools") < keys.index(conversation_key)


def test_cognition_snapshot_can_separate_the_stable_contract_from_dynamic_data():
    snapshot = TurnSnapshot("turn", 2001, 1001, [], [], [], [], [])

    assert snapshot.prompt() == INSTRUCTION + "\n" + snapshot.data_prompt()
    assert INSTRUCTION not in snapshot.data_prompt()
