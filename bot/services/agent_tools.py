"""Stable native tool registry and transport-neutral execution results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from bot.services.local_skill_contract import ACTION_CONTRACTS, FeatureRequest, valid_request


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


def scope_page_parameters(*, character: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "scope": {"type": "string", "enum": ["group", "bot"],
                  "description": "本群；仅用户明确要求总榜时使用 bot"},
        "page": {"type": "integer", "minimum": 1, "maximum": 999},
    }
    required = ["scope", "page"]
    if character:
        properties["character"] = {
            "type": "string", "minLength": 1, "maxLength": 24,
            "description": "用户明确说出的角色名",
        }
        required.append("character")
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


def archive_parameters(*, search: bool = False, page: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "target": {"type": "string", "enum": ["self", "mentioned"],
                   "description": "本人或当前消息中唯一真实 @ 的成员"},
    }
    required = ["target"]
    if search:
        properties["keyword"] = {"type": "string", "minLength": 1, "maxLength": 80}
        required.append("keyword")
    if page:
        properties["page"] = {"type": "integer", "minimum": 1, "maximum": 999}
        required.append("page")
    return {"type": "object", "properties": properties,
            "required": required, "additionalProperties": False}


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
    ToolSpec("user_help", ACTION_CONTRACTS["user_help"].description,
             empty_parameters(), "read", "direct_qq", True, "commands"),
    ToolSpec("mini_game_help", ACTION_CONTRACTS["mini_game_help"].description,
             empty_parameters(), "read", "direct_qq", True, "mini_games"),
    ToolSpec("asoul_help", ACTION_CONTRACTS["asoul_help"].description,
             empty_parameters(), "read", "direct_qq", True, "asoul"),
    ToolSpec("group_feature_status", ACTION_CONTRACTS["group_feature_status"].description,
             empty_parameters(), "read", "direct_qq", True, "group_settings"),
    ToolSpec("persona_status", ACTION_CONTRACTS["persona_status"].description,
             empty_parameters(), "read", "direct_qq", True, "persona_management"),
    ToolSpec("persona_impression", ACTION_CONTRACTS["persona_impression"].description,
             empty_parameters(), "read", "direct_qq", True, "persona_management"),
    ToolSpec("archive_records", ACTION_CONTRACTS["archive_records"].description,
             archive_parameters(page=True), "read", "direct_qq", True, "a_coast_archive"),
    ToolSpec("archive_search", ACTION_CONTRACTS["archive_search"].description,
             archive_parameters(search=True, page=True), "read", "direct_qq", True, "a_coast_archive"),
    ToolSpec("archive_profile", ACTION_CONTRACTS["archive_profile"].description,
             archive_parameters(), "read", "direct_qq", True, "a_coast_archive"),
    ToolSpec("zhijiang_status", ACTION_CONTRACTS["zhijiang_status"].description,
             empty_parameters(), "read", "direct_qq", True, "zhijiang"),
    ToolSpec("nte_help", ACTION_CONTRACTS["nte_help"].description,
             empty_parameters(), "read", "direct_qq", True, "nte_game_ui"),
    ToolSpec("nte_mint_rank", ACTION_CONTRACTS["nte_mint_rank"].description,
             scope_page_parameters(), "read", "direct_qq", True, "nte_game_ui"),
    ToolSpec("wuwa_help", ACTION_CONTRACTS["wuwa_help"].description,
             empty_parameters(), "read", "direct_qq", True, "wuwa_game_ui"),
    ToolSpec("wuwa_character_rank", ACTION_CONTRACTS["wuwa_character_rank"].description,
             scope_page_parameters(character=True), "read", "direct_qq", True, "wuwa_game_ui"),
    ToolSpec("wuwa_echo_rank", ACTION_CONTRACTS["wuwa_echo_rank"].description,
             scope_page_parameters(character=True), "read", "direct_qq", True, "wuwa_game_ui"),
    ToolSpec("wuwa_progress_rank", ACTION_CONTRACTS["wuwa_progress_rank"].description,
             scope_page_parameters(), "read", "direct_qq", True, "wuwa_game_ui"),
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
    if name in {"nte_mint_rank", "wuwa_progress_rank", "wuwa_character_rank", "wuwa_echo_rank"}:
        expected = {"scope", "page"} | ({"character"} if name.startswith("wuwa_") and name not in {"wuwa_progress_rank"} else set())
        if set(arguments) != expected or arguments.get("scope") not in {"group", "bot"}:
            raise ValueError("invalid ranking arguments")
        request = FeatureRequest.with_parameters(
            name,
            {key: value for key, value in arguments.items() if key != "scope"},
            args="总" if arguments["scope"] == "bot" else "群",
        )
        if not valid_request(request):
            raise ValueError("invalid ranking arguments")
        return request
    if name in {"archive_records", "archive_search", "archive_profile"}:
        request = FeatureRequest.with_parameters(name, arguments)
        if not valid_request(request):
            raise ValueError("invalid archive arguments")
        return request
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
