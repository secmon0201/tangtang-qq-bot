from __future__ import annotations

from bot.services.agent_context import ContextEnvelope


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
