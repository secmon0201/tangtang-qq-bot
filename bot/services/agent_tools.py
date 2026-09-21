"""Stable native tool registry and transport-neutral execution results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from bot.services.local_skill_contract import ACTION_CONTRACTS, FeatureRequest


ToolEffect = Literal["read", "write"]
ToolDelivery = Literal["model_data", "direct_qq"]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    effect: ToolEffect
    delivery: ToolDelivery
    explicit_only: bool
    owner_skill: str

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    status: Literal["delivered", "returned", "denied", "failed", "stale"]
    action: str
    message_ids: tuple[str, ...] = ()
    result_type: str = ""
    error_code: str = ""
    model_payload: Mapping[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "action": self.action,
            "result_type": self.result_type,
        }
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.message_ids:
            payload["message_ids"] = list(self.message_ids)
        if self.model_payload:
            payload["data"] = dict(self.model_payload)
        return payload


def empty_parameters() -> dict[str, Any]:
    return {"type": "object", "properties": {}, "additionalProperties": False}


def enum_parameters(name: str, values: tuple[str, ...], description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "enum": list(values),
                              "description": description}},
        "required": [name],
        "additionalProperties": False,
    }


KNOWLEDGE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "search_zhijiang_knowledge",
        "检索本地枝江百科数据库；只用于回答 A-SOUL、枝江企划、成员、作品和直播历史问题。",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "自然语言问题或关键词"},
        }, "required": ["query"], "additionalProperties": False},
        "read", "model_data", False, "local_knowledge",
    ),
    ToolSpec(
        "search_mingchao_meme_culture",
        "检索本地鸣潮梗文化库；只用于解释公开游戏黑话和社区梗。",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "自然语言问题或关键词"},
        }, "required": ["query"], "additionalProperties": False},
        "read", "model_data", False, "local_knowledge",
    ),
)


ACTION_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec("ranking", ACTION_CONTRACTS["ranking"].description, {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["日", "周", "月", "总"]},
            "scope": {"type": "string", "enum": ["group", "cluster"]},
        },
        "required": ["period", "scope"], "additionalProperties": False,
    }, "read", "direct_qq", True, "commands"),
    ToolSpec("today_live", ACTION_CONTRACTS["today_live"].description,
             empty_parameters(), "read", "direct_qq", True, "asoul"),
    ToolSpec("tomorrow_live", ACTION_CONTRACTS["tomorrow_live"].description,
             empty_parameters(), "read", "direct_qq", True, "asoul"),
    ToolSpec("week_live", ACTION_CONTRACTS["week_live"].description,
             empty_parameters(), "read", "direct_qq", True, "asoul"),
    ToolSpec("zhijiang_schedule", ACTION_CONTRACTS["zhijiang_schedule"].description,
             empty_parameters(), "read", "direct_qq", True, "zhijiang"),
    ToolSpec("mini_game_roulette", ACTION_CONTRACTS["mini_game_roulette"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "mini_games"),
    ToolSpec("mini_game_bomb", ACTION_CONTRACTS["mini_game_bomb"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "mini_games"),
    ToolSpec("mini_game_dice", ACTION_CONTRACTS["mini_game_dice"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "mini_games"),
    ToolSpec("mini_game_guess", ACTION_CONTRACTS["mini_game_guess"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "mini_games"),
    ToolSpec("nte_rank", ACTION_CONTRACTS["nte_rank"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "nte_game_ui"),
    ToolSpec("wuwa_rank", ACTION_CONTRACTS["wuwa_rank"].description,
             enum_parameters("scope", ("group", "bot"), "本群或明确机器人总榜"),
             "read", "direct_qq", True, "wuwa_game_ui"),
    ToolSpec("wife_personal", ACTION_CONTRACTS["wife_personal"].description,
             empty_parameters(), "read", "direct_qq", True, "today_wife"),
    ToolSpec("wife_group", ACTION_CONTRACTS["wife_group"].description,
             empty_parameters(), "read", "direct_qq", True, "today_wife"),
    ToolSpec("denia_gallery", ACTION_CONTRACTS["denia_gallery"].description,
             empty_parameters(), "read", "direct_qq", True, "denia_gallery"),
)

BASE_TOOL_SPECS = (*KNOWLEDGE_TOOL_SPECS, *ACTION_TOOL_SPECS)
TOOL_REGISTRY = {spec.name: spec for spec in BASE_TOOL_SPECS}
ACTION_TOOL_NAMES = frozenset(spec.name for spec in ACTION_TOOL_SPECS)


def tool_schemas(*, include_actions: bool) -> tuple[dict[str, Any], ...]:
    specs = BASE_TOOL_SPECS if include_actions else KNOWLEDGE_TOOL_SPECS
    return tuple(spec.schema() for spec in specs)


def parse_action_tool_call(call: Mapping[str, Any]) -> FeatureRequest:
    name = str(call.get("name") or "")
    if name not in ACTION_TOOL_NAMES:
        raise ValueError("unknown action tool")
    try:
        arguments = json.loads(str(call.get("arguments") or "{}"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid action tool JSON") from exc
    if not isinstance(arguments, dict):
        raise ValueError("action tool arguments must be an object")
    if name == "ranking":
        if (
            set(arguments) != {"period", "scope"}
            or arguments.get("period") not in {"日", "周", "月", "总"}
            or arguments.get("scope") not in {"group", "cluster"}
        ):
            raise ValueError("invalid ranking arguments")
        return FeatureRequest(
            name,
            str(arguments.get("period") or ""),
            str(arguments.get("scope") or "") == "cluster",
        )
    if name in {"mini_game_roulette", "mini_game_bomb", "mini_game_dice",
                "mini_game_guess", "nte_rank", "wuwa_rank"}:
        if set(arguments) != {"scope"} or arguments.get("scope") not in {"group", "bot"}:
            raise ValueError("invalid ranking scope")
        return FeatureRequest(name, "总" if arguments["scope"] == "bot" else "群")
    if arguments:
        raise ValueError("tool does not accept arguments")
    return FeatureRequest(name)


__all__ = [
    "ACTION_TOOL_NAMES",
    "ACTION_TOOL_SPECS",
    "BASE_TOOL_SPECS",
    "KNOWLEDGE_TOOL_SPECS",
    "TOOL_REGISTRY",
    "ToolExecutionResult",
    "ToolSpec",
    "parse_action_tool_call",
    "tool_schemas",
]
