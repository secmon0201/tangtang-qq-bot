"""Offline, privacy-safe replay for layered Agent context payloads."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from bot.services.agent_context import ContextEnvelope, stable_hash
from bot.services.agent_tools import KNOWLEDGE_TOOL_SPECS, tool_schemas
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
REPORT_VERSION = "agent-context-replay-v1"


def _config(api_style: str) -> TangtangConfig:
    return replace(
        TangtangConfig.disabled("offline_replay"),
        enabled=True,
        api_style=api_style,
        model="synthetic-replay-model",
        reasoning_effort="low",
        max_output_tokens=64,
    )


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping) and item.get("type") in {"input_text", "output_text"}
    )


def semantic_items(api_style: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Normalize provider payloads to the transport-neutral semantic order."""

    items: list[dict[str, Any]] = []
    if api_style == "responses":
        raw_items = payload.get("input")
        if not isinstance(raw_items, list):
            return ()
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                items.append({
                    "type": "tool_call",
                    "call_id": str(item.get("call_id") or ""),
                    "name": str(item.get("name") or ""),
                    "arguments": str(item.get("arguments") or "{}"),
                })
            elif item_type == "function_call_output":
                items.append({
                    "type": "tool_result",
                    "call_id": str(item.get("call_id") or ""),
                    "output": str(item.get("output") or ""),
                })
            elif item.get("role"):
                items.append({
                    "type": "message",
                    "role": str(item.get("role")),
                    "content": _response_text(item.get("content")),
                })
        return tuple(items)

    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        return ()
    for item in raw_messages:
        if not isinstance(item, Mapping):
            continue
        calls = item.get("tool_calls")
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                if not isinstance(function, Mapping):
                    function = {}
                items.append({
                    "type": "tool_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or "{}"),
                })
            continue
        if item.get("role") == "tool":
            items.append({
                "type": "tool_result",
                "call_id": str(item.get("tool_call_id") or ""),
                "output": str(item.get("content") or ""),
            })
            continue
        items.append({
            "type": "message",
            "role": str(item.get("role") or ""),
            "content": _response_text(item.get("content")),
        })
    return tuple(items)


def _payload(api_style: str, envelope: ContextEnvelope, tools: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    config = _config(api_style)
    builder = (
        TangtangProvider._responses_payload
        if api_style == "responses"
        else TangtangProvider._chat_payload
    )
    return builder(
        config,
        envelope.static_text,
        envelope.current_text,
        tools=tools,
        envelope=envelope,
    )


def _legacy_payload(api_style: str, persona: str, prompt: str) -> dict[str, Any]:
    config = _config(api_style)
    builder = (
        TangtangProvider._responses_payload
        if api_style == "responses"
        else TangtangProvider._chat_payload
    )
    return builder(
        config,
        persona,
        prompt,
        tools=tuple(spec.schema() for spec in KNOWLEDGE_TOOL_SPECS),
    )


def _common_prefix_chars(first: str, second: str) -> int:
    for index, (left, right) in enumerate(zip(first, second)):
        if left != right:
            return index
    return min(len(first), len(second))


def _wire_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_report(*, stable_chars: int = 16_000) -> dict[str, Any]:
    stable_chars = max(4_096, min(int(stable_chars), 100_000))
    stable_unit = "stable synthetic context line 0123456789\n"
    persona = ("[synthetic persona]\n" + stable_unit * (stable_chars // len(stable_unit) + 1))[
        :stable_chars
    ]
    tools = tool_schemas(include_actions=True)
    first = ContextEnvelope.create(
        persona=persona,
        dynamic_status="synthetic_state=first",
        current_input="synthetic current input one",
        tools=tools,
    )
    prior_items = (
        {"type": "message", "role": "user", "content": first.current_text},
        {"type": "message", "role": "assistant", "content": "[reply] synthetic response"},
        {"type": "tool_call", "call_id": "synthetic-call", "name": "user_help", "arguments": "{}"},
        {"type": "tool_result", "call_id": "synthetic-call", "output": '{"status":"delivered"}'},
        {"type": "message", "role": "assistant", "content": "[reply] synthetic follow-up"},
    )
    second = ContextEnvelope.create(
        persona=persona,
        conversation_items=prior_items,
        dynamic_status="synthetic_state=second",
        current_input="synthetic current input two",
        tools=tools,
    )

    style_reports: dict[str, Any] = {}
    normalized: dict[str, tuple[dict[str, Any], ...]] = {}
    layered_suffixes: list[int] = []
    legacy_suffixes: list[int] = []
    legacy_sizes: list[int] = []
    layered_sizes: list[int] = []
    legacy_stable = ("[legacy repeated context]\n" + stable_unit * (stable_chars // len(stable_unit) + 1))[
        :stable_chars
    ]
    legacy_first_prompt = "request=first\n" + legacy_stable + "\ncurrent=one"
    legacy_second_prompt = (
        "request=second\n" + legacy_stable
        + "\nprior_user=one\nprior_assistant=synthetic\ncurrent=two"
    )

    for api_style in ("responses", "chat_completions"):
        first_payload = _payload(api_style, first, tools)
        second_payload = _payload(api_style, second, tools)
        first_items = semantic_items(api_style, first_payload)
        second_items = semantic_items(api_style, second_payload)
        expected_first = first.canonical_semantic_items()
        expected_second = second.canonical_semantic_items()
        strict_prefix = second_items[: len(first_items)] == first_items
        if first_items != expected_first or second_items != expected_second or not strict_prefix:
            raise RuntimeError(f"{api_style} semantic replay failed")
        normalized[api_style] = second_items

        legacy_first = _wire_json(_legacy_payload(api_style, persona, legacy_first_prompt))
        legacy_second = _wire_json(_legacy_payload(api_style, persona, legacy_second_prompt))
        layered_first = _wire_json(first_payload)
        layered_second = _wire_json(second_payload)
        legacy_suffix = len(legacy_second) - _common_prefix_chars(legacy_first, legacy_second)
        layered_suffix = len(layered_second) - _common_prefix_chars(layered_first, layered_second)
        legacy_suffixes.append(legacy_suffix)
        layered_suffixes.append(layered_suffix)
        legacy_sizes.append(len(legacy_second))
        layered_sizes.append(len(layered_second))
        style_reports[api_style] = {
            "turn1_item_count": len(first_items),
            "turn2_item_count": len(second_items),
            "strict_append_prefix": strict_prefix,
            "turn1_payload_hash": stable_hash(first_payload),
            "turn2_payload_hash": stable_hash(second_payload),
            "legacy_turn2_serialized_chars": len(legacy_second),
            "layered_turn2_serialized_chars": len(layered_second),
            "legacy_changed_suffix_chars": legacy_suffix,
            "layered_changed_suffix_chars": layered_suffix,
        }

    legacy_non_cached = max(legacy_suffixes)
    layered_non_cached = max(layered_suffixes)
    reduction = (
        round((legacy_non_cached - layered_non_cached) * 100 / legacy_non_cached, 2)
        if legacy_non_cached else 0.0
    )
    return {
        "report_version": REPORT_VERSION,
        "privacy": "hashes_and_counts_only",
        "static_prefix_hash": first.static_prefix_hash,
        "tool_schema_hash": first.tool_schema_hash,
        "tool_count": len(tools),
        "static_prefix_chars": first.layer_sizes()["static_prefix_chars"],
        "api_styles": style_reports,
        "cross_api_semantic_order_equal": (
            normalized["responses"] == normalized["chat_completions"]
        ),
        "shadow": {
            "candidate_built_locally": True,
            "candidate_tool_count": len(tools),
            "legacy_provider_tool_count": len(KNOWLEDGE_TOOL_SPECS),
            "additional_paid_requests": 0,
        },
        "comparison": {
            "legacy_serialized_chars_max": max(legacy_sizes),
            "layered_serialized_chars_max": max(layered_sizes),
            "legacy_changed_suffix_chars_max": legacy_non_cached,
            "layered_changed_suffix_chars_max": layered_non_cached,
            "changed_suffix_reduction_percent": reduction,
            "estimate_only": True,
        },
    }


def _report_path(raw: Path) -> Path:
    path = raw if raw.is_absolute() else ROOT / raw
    resolved = path.resolve()
    try:
        resolved.relative_to(REPORT_DIR.resolve())
    except ValueError as exc:
        raise ValueError("output must be inside reports/") from exc
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stable-chars", type=int, default=16_000)
    parser.add_argument("--output", type=Path, default=Path("reports/agent-context-replay.json"))
    args = parser.parse_args()
    try:
        output = _report_path(args.output)
        report = build_report(stable_chars=args.stable_chars)
    except (RuntimeError, ValueError) as exc:
        print(f"Agent context replay failed: {exc}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Agent context replay passed: "
        f"tools={report['tool_count']}, "
        f"changed-suffix-reduction={report['comparison']['changed_suffix_reduction_percent']}%, "
        f"report={output.relative_to(ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
