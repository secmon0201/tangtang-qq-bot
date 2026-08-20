from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx
import truststore
from dotenv import dotenv_values
from nonebot import logger

from bot.config import ROOT, settings
from bot.services.qq_platform import call_qq_action
from bot.services.replies import quote_message
from bot.services.tangtang_db import TangtangDb
from bot.services.knowledge_db import FORBIDDEN_LOCAL_TERMS
from bot.services.mingchao_meme_culture import search as mingchao_meme_search
from bot.services.zhijiang_knowledge import search as zhijiang_search

truststore.inject_into_ssl()

RESOURCE_DIR = ROOT / "bot" / "resources" / "tangtang"
USAGE_DIR = ROOT / "data" / "tangtang" / "usage"
PERSONA_PATH = RESOURCE_DIR / "persona.md"
HARD_BLACKLIST_PATH = RESOURCE_DIR / "hard_blacklist.txt"
SOFT_BLACKLIST_PATH = RESOURCE_DIR / "soft_blacklist.txt"
LINES_PATH = RESOURCE_DIR / "lines.txt"

GROUP_CONTEXT_MESSAGES = 30
CALL_REPEAT_MERGE_SECONDS = 60
ZHIJIANG_KNOWLEDGE_LIMIT = 3
ZHIJIANG_KNOWLEDGE_MAX_CHARS = 700
MINGCHAO_MEME_LIMIT = 3
MINGCHAO_MEME_MAX_CHARS = 700
LOCAL_KNOWLEDGE_MAX_CHARS = 1200
TOOL_MAX_RESULT_CHARS = 700

TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "name": "search_zhijiang_knowledge",
        "description": (
            "检索本地枝江百科数据库（A-SOUL 与枝江娱乐企划资料）。"
            "涉及嘉然、贝拉、乃琳、心宜、思诺、小心思、闪耀舞台、灵境少女、银河商店、"
            "枝江历史、直播时间线等问题时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然语言问题或关键词，例如：嘉然是谁"}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_mingchao_meme_culture",
        "description": (
            "检索本地鸣潮梗文化库（游戏黑话、玩家社区梗、枝江二创社区梗）。"
            "涉及鸣潮公式、牢卡、雪豹、小土豆、潮友、乃琳直播鸣潮、贝拉晕3D、战双联动等问题时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "自然语言问题或关键词，例如：鸣潮公式是什么"}
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
)


def _local_member_name(group_id: int, user_id: int) -> str:
    """Group card/nickname from the local member cache; empty when unknown."""
    try:
        from bot.services.runtime import database

        return database().group_member_name(group_id, user_id)
    except Exception:
        return ""


async def resolve_at_labels(
    bot: Any, event: Any, *, use_api: bool = True
) -> dict[str, str]:
    """Map @ QQ ids in a message to readable names (card > nickname > QQ)."""

    labels: dict[str, str] = {}
    seen: set[str] = set()
    for segment in getattr(event, "message", ()):
        if str(getattr(segment, "type", "") or "") != "at":
            continue
        qq = str((getattr(segment, "data", None) or {}).get("qq") or "")
        if not qq or qq in seen:
            continue
        seen.add(qq)
        if qq == "all":
            labels[qq] = "全体成员"
            continue
        inline = str(
            (getattr(segment, "data", None) or {}).get("text") or ""
        ).lstrip("@").strip()
        if inline:
            labels[qq] = inline
            continue
        if bot is not None and str(getattr(bot, "self_id", "") or "") == qq:
            labels[qq] = "糖糖"
            continue
        if qq.isdigit():
            name = _local_member_name(int(event.group_id), int(qq))
            if not name and use_api and bot is not None:
                try:
                    from bot.services.qq_platform import qq_platform

                    info = await qq_platform(bot).member_info(int(event.group_id), int(qq))
                    name = str(
                        (info or {}).get("card") or (info or {}).get("nickname") or ""
                    )
                except Exception:
                    name = ""
            if name:
                labels[qq] = name
                continue
        labels[qq] = qq
    return labels


def render_message_text(
    message: Any, labels: Mapping[str, str] | None = None
) -> str:
    """Plain text of a OneBot message with @ mentions kept as readable labels."""

    labels = labels or {}
    parts: list[str] = []
    for segment in getattr(message, "message", message):
        seg_type = str(getattr(segment, "type", "") or "")
        if seg_type == "text":
            parts.append(str(segment.data.get("text") or ""))
        elif seg_type == "at":
            qq = str((segment.data or {}).get("qq") or "")
            parts.append(f"@{labels.get(qq) or qq}")
    return "".join(parts).strip()


def _safe_tool_entry(entry: Any) -> dict[str, str] | None:
    """Defense-in-depth filter so tool results never expose forbidden terms."""
    related = getattr(entry, "related", ())
    related_text = " ".join(title for _ref, title in related)
    text = (
        f"{entry.title} {entry.summary} {entry.source_name} "
        f"{entry.source_url} {related_text}"
    )
    if any(term in text for term in FORBIDDEN_LOCAL_TERMS):
        return None
    result: dict[str, Any] = {
        "title": entry.title,
        "summary": entry.summary,
        "source_name": entry.source_name,
        "source_url": entry.source_url,
    }
    if related:
        result["related"] = [
            {"ref": ref, "title": title} for ref, title in related
        ]
    return result


BLACK_MEME_INSTRUCTIONS: tuple[tuple[str, str], ...] = (
    (
        "珈乐",
        "[黑梗应对] 本条涉及被屏蔽的旧成员相关话题：不要装傻，也不要展开，"
        "用你自己的话表达“不谈这个fifa人物”类似的意思，不解释、不输出任何历史细节，"
        "不要复述相关称呼。",
    ),
    (
        "皇珈骑士",
        "[黑梗应对] 本条涉及被屏蔽的旧成员相关话题：不要装傻，也不要展开，"
        "用你自己的话表达“不谈这个fifa人物”类似的意思，不解释、不输出任何历史细节，"
        "不要复述相关称呼。",
    ),
    (
        "皇珈",
        "[黑梗应对] 本条涉及被屏蔽的旧成员相关话题：不要装傻，也不要展开，"
        "用你自己的话表达“不谈这个fifa人物”类似的意思，不解释、不输出任何历史细节，"
        "不要复述相关称呼。",
    ),
    (
        "李滇滇",
        "[黑梗应对] 本条涉及李滇滇相关话题：不要装傻，也不评价，"
        "用类似“人各有志，各自安好”的话回应，不扩散节奏细节。",
    ),
)


def _black_meme_instruction(text: str) -> str | None:
    """Prompt instruction for known black-meme topics; principles are fixed."""

    for key, instruction in BLACK_MEME_INSTRUCTIONS:
        if key in text:
            return instruction
    return None


DEFAULT_PERSONA = (
    "你是糖糖，一个会认真翻聊天记录、又有点自己小脾气的嘉心糖观察员。"
    "第一人称只用糖糖，回复用自然口语短句。"
    "第一行先写 [接话] 或 [沉默]，[接话] 时第二行起写正文。"
)

_QUESTION_RE = re.compile(
    r"[？?]|吗|呢|怎么|什么|为什么|如何|多少|几|谁|哪|能不能|"
    r"可以|帮我|求|告诉|解释|介绍|说说|啥|什么时候|在哪|哪儿|几点"
)
_CASUAL_RE = re.compile(
    r"你好|您好|嗨|哈喽|hello|hi|在吗|在不在|早安|晚安|早上好|"
    r"晚上好|中午好|哈哈|嘿嘿|嘻嘻"
)


def _raw(values: Mapping[str, Any], name: str, default: str = "") -> str:
    value = values.get(name)
    return default if value is None else str(value).strip()


def _bool(values: Mapping[str, Any], name: str, default: bool) -> bool:
    value = _raw(values, name, "true" if default else "false").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _float(values: Mapping[str, Any], name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(_raw(values, name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _int(values: Mapping[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(_raw(values, name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _ids(values: Mapping[str, Any], name: str, maximum: int = 10000) -> frozenset[int]:
    raw = _raw(values, name)
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"{name} contains an invalid QQ ID")
        result.add(int(item))
    if len(result) > maximum:
        raise ValueError(f"{name} contains too many IDs")
    return frozenset(result)


def _ordered_ids(values: Mapping[str, Any], name: str, maximum: int = 10000) -> tuple[int, ...]:
    raw = _raw(values, name)
    result: list[int] = []
    seen: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"{name} contains an invalid QQ ID")
        group_id = int(item)
        if group_id not in seen:
            seen.add(group_id)
            result.append(group_id)
    if len(result) > maximum:
        raise ValueError(f"{name} contains too many IDs")
    return tuple(result)


@dataclass(frozen=True, slots=True)
class TangtangConfig:
    enabled: bool
    mode: str
    call_keyword: str
    group_ids: frozenset[int]
    group_order: tuple[int, ...]
    group_context_messages: int
    ignore_probability: float
    call_ignore_probability_by_group: dict[int, float]
    required_call_reply_group_ids: frozenset[int]
    soft_blacklist_ignore_probability: float
    c_probability: float
    proactive_enabled: bool
    proactive_probability: float
    proactive_probability_by_group: dict[int, float]
    proactive_cooldown_seconds: int
    proactive_cooldown_seconds_by_group: dict[int, int]
    proactive_message_interval: int
    proactive_message_interval_by_group: dict[int, int]
    api_url: str
    api_key: str
    api_style: str
    model: str
    reasoning_effort: str
    timeout_seconds: int
    max_input_chars: int
    max_output_tokens: int
    max_response_chars: int
    history_messages: int
    history_chars: int
    disabled_reason: str = ""
    tools_enabled: bool = True
    tool_loop_max: int = 3

    @classmethod
    def disabled(cls, reason: str = "TANGTANG_ENABLED=false") -> "TangtangConfig":
        return cls(
            enabled=False,
            mode="d",
            call_keyword="糖糖",
            group_ids=frozenset(),
            group_order=(),
            group_context_messages=GROUP_CONTEXT_MESSAGES,
            ignore_probability=0.10,
            call_ignore_probability_by_group={},
            required_call_reply_group_ids=frozenset(),
            soft_blacklist_ignore_probability=0.0,
            c_probability=0.40,
            proactive_enabled=False,
            proactive_probability=0.02,
            proactive_probability_by_group={},
            proactive_cooldown_seconds=1800,
            proactive_cooldown_seconds_by_group={},
            proactive_message_interval=30,
            proactive_message_interval_by_group={},
            api_url="",
            api_key="",
            api_style="responses",
            model="",
            reasoning_effort="none",
            timeout_seconds=0,
            max_input_chars=0,
            max_output_tokens=0,
            max_response_chars=0,
            history_messages=10,
            history_chars=1000,
            disabled_reason=reason,
        )

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, Any],
        managed_group_ids: tuple[int, ...],
    ) -> "TangtangConfig":
        enabled = _bool(values, "TANGTANG_ENABLED", False)
        mode = _raw(values, "TANGTANG_MODE", "d").lower()
        if mode not in {"d", "c"}:
            raise ValueError("TANGTANG_MODE must be d or c")
        keyword = _raw(values, "TANGTANG_CALL_KEYWORD", "糖糖")
        group_order = _ordered_ids(values, "TANGTANG_GROUP_IDS", maximum=10)
        group_ids = frozenset(group_order)
        group_context_messages = _int(
            values, "TANGTANG_GROUP_CONTEXT_MESSAGES", GROUP_CONTEXT_MESSAGES, 1, 100
        )
        ignore_probability = _float(values, "TANGTANG_IGNORE_PROBABILITY", 0.10, 0.0, 1.0)
        soft_blacklist_ignore_probability = _float(
            values, "TANGTANG_SOFT_BLACKLIST_IGNORE_PROBABILITY", 0.0, 0.0, 1.0
        )
        c_probability = _float(values, "TANGTANG_C_PROBABILITY", 0.40, 0.0, 1.0)
        proactive_enabled = _bool(values, "TANGTANG_PROACTIVE_ENABLED", False)
        def group_values(name: str, default: object, parser) -> dict[int, object]:
            if not group_order:
                return {}
            raw = values.get(name)
            if raw is None:
                return {group_id: default for group_id in group_order}
            items = [str(item).strip() for item in str(raw).split(",")]
            if len(items) != len(group_order) or any(not item for item in items):
                raise ValueError(
                    f"{name} must provide exactly one value for each TANGTANG_GROUP_IDS entry"
                )
            return {group_id: parser(item) for group_id, item in zip(group_order, items)}

        def group_float(name: str, default: float, minimum: float, maximum: float) -> dict[int, float]:
            def parse(item: str) -> float:
                value = float(item)
                if not minimum <= value <= maximum:
                    raise ValueError(f"{name} must be between {minimum} and {maximum}")
                return value

            return group_values(name, default, parse)  # type: ignore[return-value]

        def group_integer(name: str, default: int, minimum: int, maximum: int) -> dict[int, int]:
            def parse(item: str) -> int:
                value = int(item)
                if not minimum <= value <= maximum:
                    raise ValueError(f"{name} must be between {minimum} and {maximum}")
                return value

            return group_values(name, default, parse)  # type: ignore[return-value]

        call_ignore_probability_by_group = group_float(
            "TANGTANG_CALL_IGNORE_PROBABILITY", ignore_probability, 0.0, 1.0
        )
        required_call_reply_group_ids = frozenset(
            _ordered_ids(values, "TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS", maximum=10)
        )

        proactive_probability_by_group = group_float(
            "TANGTANG_PROACTIVE_PROBABILITY", 0.02, 0.0, 1.0
        )
        proactive_cooldown_seconds_by_group = group_integer(
            "TANGTANG_PROACTIVE_COOLDOWN_SECONDS", 1800, 0, 86400
        )
        proactive_message_interval_by_group = group_integer(
            "TANGTANG_PROACTIVE_MESSAGE_INTERVAL", 30, 0, 10000
        )
        if group_order:
            first_group_id = group_order[0]
            proactive_probability = proactive_probability_by_group[first_group_id]
            proactive_cooldown_seconds = proactive_cooldown_seconds_by_group[first_group_id]
            proactive_message_interval = proactive_message_interval_by_group[first_group_id]
        else:
            proactive_probability = 0.02
            proactive_cooldown_seconds = 1800
            proactive_message_interval = 30
        history_messages = _int(values, "TANGTANG_HISTORY_MESSAGES", 10, 0, 100)
        history_chars = _int(values, "TANGTANG_HISTORY_CHARS", 1000, 0, 24000)
        tools_enabled = _bool(values, "TANGTANG_TOOLS_ENABLED", True)
        tool_loop_max = _int(values, "TANGTANG_TOOL_LOOP_MAX", 3, 1, 8)
        if not enabled:
            return cls(
                enabled=False,
                mode=mode,
                call_keyword=keyword,
                group_ids=group_ids,
                group_order=group_order,
                group_context_messages=group_context_messages,
                ignore_probability=ignore_probability,
                call_ignore_probability_by_group=call_ignore_probability_by_group,
                required_call_reply_group_ids=required_call_reply_group_ids,
                soft_blacklist_ignore_probability=soft_blacklist_ignore_probability,
                c_probability=c_probability,
                proactive_enabled=proactive_enabled,
                proactive_probability=proactive_probability,
                proactive_probability_by_group=proactive_probability_by_group,
                proactive_cooldown_seconds=proactive_cooldown_seconds,
                proactive_cooldown_seconds_by_group=proactive_cooldown_seconds_by_group,
                proactive_message_interval=proactive_message_interval,
                proactive_message_interval_by_group=proactive_message_interval_by_group,
                api_url="",
                api_key="",
                api_style="responses",
                model="",
                reasoning_effort="none",
                timeout_seconds=0,
                max_input_chars=0,
                max_output_tokens=0,
                max_response_chars=0,
                history_messages=history_messages,
                history_chars=history_chars,
                disabled_reason="TANGTANG_ENABLED=false",
                tools_enabled=tools_enabled,
                tool_loop_max=tool_loop_max,
            )
        if not group_ids:
            raise ValueError("TANGTANG_GROUP_IDS is required when TANGTANG_ENABLED=true")
        invalid = sorted(group_ids - set(managed_group_ids))
        if invalid:
            raise ValueError("TANGTANG_GROUP_IDS contains groups outside MANAGED_GROUP_IDS")
        invalid_required = sorted(required_call_reply_group_ids - group_ids)
        if invalid_required:
            raise ValueError(
                "TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS must be within TANGTANG_GROUP_IDS"
            )
        api_url = _raw(values, "TANGTANG_API_URL")
        api_key = _raw(values, "TANGTANG_API_KEY")
        model = _raw(values, "TANGTANG_MODEL", "deepseek-v4-flash")
        if not api_url or not api_key or not model:
            raise ValueError("TANGTANG_API_URL, TANGTANG_API_KEY and TANGTANG_MODEL are required")
        api_style = _raw(values, "TANGTANG_API_STYLE", "responses").lower()
        if api_style not in {"responses", "chat_completions"}:
            raise ValueError("TANGTANG_API_STYLE must be responses or chat_completions")
        reasoning_effort = _raw(values, "TANGTANG_REASONING_EFFORT", "none").lower()
        if reasoning_effort not in {"none", "low", "high", "max"}:
            raise ValueError("TANGTANG_REASONING_EFFORT is invalid")
        return cls(
            enabled=True,
            mode=mode,
            call_keyword=keyword,
            group_ids=group_ids,
            group_order=group_order,
            group_context_messages=group_context_messages,
            ignore_probability=ignore_probability,
            call_ignore_probability_by_group=call_ignore_probability_by_group,
            required_call_reply_group_ids=required_call_reply_group_ids,
            soft_blacklist_ignore_probability=soft_blacklist_ignore_probability,
            c_probability=c_probability,
            proactive_enabled=proactive_enabled,
            proactive_probability=proactive_probability,
            proactive_probability_by_group=proactive_probability_by_group,
            proactive_cooldown_seconds=proactive_cooldown_seconds,
            proactive_cooldown_seconds_by_group=proactive_cooldown_seconds_by_group,
            proactive_message_interval=proactive_message_interval,
            proactive_message_interval_by_group=proactive_message_interval_by_group,
            api_url=api_url,
            api_key=api_key,
            api_style=api_style,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=_int(values, "TANGTANG_TIMEOUT_SECONDS", 30, 1, 120),
            max_input_chars=_int(values, "TANGTANG_MAX_INPUT_CHARS", 2000, 300, 24000),
            max_output_tokens=_int(values, "TANGTANG_MAX_OUTPUT_TOKENS", 512, 16, 24000),
            max_response_chars=_int(values, "TANGTANG_MAX_RESPONSE_CHARS", 1200, 40, 24000),
            history_messages=history_messages,
            history_chars=history_chars,
            tools_enabled=tools_enabled,
            tool_loop_max=tool_loop_max,
        )

    def proactive_values_for(self, group_id: int) -> tuple[float, int, int]:
        group_id = int(group_id)
        try:
            return (
                self.proactive_probability_by_group[group_id],
                self.proactive_cooldown_seconds_by_group[group_id],
                self.proactive_message_interval_by_group[group_id],
            )
        except KeyError as exc:
            raise ValueError("group is outside the Tangtang scope") from exc

    def call_ignore_probability_for(self, group_id: int) -> float:
        try:
            return self.call_ignore_probability_by_group[int(group_id)]
        except KeyError as exc:
            raise ValueError("group is outside the Tangtang scope") from exc

    def requires_call_reply(self, group_id: int) -> bool:
        return int(group_id) in self.required_call_reply_group_ids


class TangtangConfigLoader:
    """Hot-reloadable loader for TANGTANG_* .env entries (mtime based)."""

    def __init__(
        self,
        path: Path | None = None,
        managed_group_ids: tuple[int, ...] | None = None,
    ) -> None:
        self.path = path or (ROOT / ".env")
        self._managed_group_ids = managed_group_ids
        self._signature: tuple[int, int] | None = None
        self._config = TangtangConfig.disabled()

    def load(self) -> TangtangConfig:
        try:
            stat = self.path.stat()
        except OSError:
            return self._config
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return self._config
        self._signature = signature
        try:
            values = dotenv_values(self.path)
            managed = (
                self._managed_group_ids
                if self._managed_group_ids is not None
                else settings.managed_group_ids
            )
            self._config = TangtangConfig.from_values(values, managed)
            logger.info(f"Tangtang configuration reloaded; enabled={self._config.enabled}")
        except (OSError, TypeError, ValueError) as exc:
            self._config = TangtangConfig.disabled("configuration_error")
            logger.warning(f"Tangtang configuration invalid; feature disabled: {exc}")
        return self._config


class _TextFileCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._signature: tuple[int, int] | None = None
        self._text = ""

    def text(self) -> str:
        try:
            stat = self.path.stat()
        except OSError:
            return self._text
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return self._text
        self._signature = signature
        try:
            self._text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            pass
        return self._text


def _line_entries(text: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def _term_set(text: str) -> frozenset[str]:
    return frozenset(line.lower() for line in _line_entries(text) if not line.startswith("re:"))


def _regex_patterns(text: str) -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for line in _line_entries(text):
        if line.startswith("re:"):
            try:
                patterns.append(re.compile(line[3:], re.IGNORECASE))
            except re.error as exc:
                logger.warning("Tangtang blacklist regex ignored {}: {}", line, exc)
    return tuple(patterns)


def _matches(
    terms: frozenset[str],
    patterns: tuple[re.Pattern[str], ...],
    text: str,
) -> bool:
    lowered = text.lower()
    if any(term in lowered for term in terms):
        return True
    return any(pattern.search(text) for pattern in patterns)


def _match_detail(
    terms: frozenset[str],
    patterns: tuple[re.Pattern[str], ...],
    text: str,
) -> str | None:
    """Return which blacklist term or regex matched, without message content."""

    lowered = text.lower()
    for term in sorted(terms):
        if term in lowered:
            return f"term:{term}"
    for pattern in patterns:
        if pattern.search(text):
            return f"re:{pattern.pattern}"
    return None


def _line_list(text: str) -> tuple[str, ...]:
    lines = tuple(
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    return lines


def parse_decision(text: str) -> tuple[bool, str]:
    """Parse [接话]/[沉默] output; fall back to treating bare text as a reply."""

    stripped = text.strip()
    if not stripped:
        return False, ""
    # The model's final decision is the last marker it emits; reasoning text
    # (if it ever leaks into content) may contain earlier markers.
    last_reply = stripped.rfind("[接话]")
    last_silent = stripped.rfind("[沉默]")
    if last_reply >= 0 and last_reply > last_silent:
        return True, stripped[last_reply + len("[接话]") :].strip()
    if last_silent >= 0:
        return False, ""
    if "沉默" in stripped[:8]:
        return False, ""
    return True, stripped


def classify_call(text: str) -> str:
    if _CASUAL_RE.search(text):
        return "casual"
    if _QUESTION_RE.search(text):
        return "question"
    return "statement"


@dataclass(frozen=True, slots=True)
class AgentResult:
    text: str
    tool_calls: tuple[dict[str, str], ...]
    usage: dict[str, Any]


class TangtangProvider:
    @staticmethod
    def endpoint(config: TangtangConfig) -> str:
        url = config.api_url.rstrip("/")
        suffix = "/responses" if config.api_style == "responses" else "/chat/completions"
        return url if url.endswith(suffix) else url + suffix

    async def generate(
        self,
        config: TangtangConfig,
        persona: str,
        prompt: str,
    ) -> tuple[str, dict[str, Any]]:
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        if config.api_style == "responses":
            payload = self._responses_payload(config, persona, prompt)
        else:
            payload = self._chat_payload(config, persona, prompt)
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(self.endpoint(config), headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        text = self._extract_text(data)[: config.max_response_chars].strip()
        usage = self._extract_usage(data)
        usage["latency_ms"] = round((time.monotonic() - started) * 1000)
        return text, usage

    async def generate_agent(
        self,
        config: TangtangConfig,
        persona: str,
        prompt: str,
        tools: tuple[dict[str, Any], ...] = (),
        history: tuple[dict[str, Any], ...] = (),
    ) -> AgentResult:
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        if config.api_style == "responses":
            payload = self._responses_payload(config, persona, prompt, tools=tools, history=history)
        else:
            payload = self._chat_payload(config, persona, prompt, tools=tools, history=history)
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(self.endpoint(config), headers=headers, json=payload)
            if response.status_code >= 400 and tools and self._unsupported_tools_error(response):
                logger.warning(
                    "Tangtang API rejected tool parameters; falling back to plain generation"
                )
                text, usage = await self.generate(config, persona, prompt)
                return AgentResult(text=text, tool_calls=(), usage=usage)
            response.raise_for_status()
            data = response.json()
        text = self._extract_text(data)[: config.max_response_chars].strip()
        tool_calls = self._extract_tool_calls(data)
        usage = self._extract_usage(data)
        usage["latency_ms"] = round((time.monotonic() - started) * 1000)
        return AgentResult(text=text, tool_calls=tool_calls, usage=usage)

    @staticmethod
    def _responses_payload(
        config: TangtangConfig,
        persona: str,
        prompt: str,
        *,
        tools: tuple[dict[str, Any], ...] = (),
        history: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        input_items: list[dict[str, Any]] = [
            {"role": "system", "content": [{"type": "input_text", "text": persona}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ]
        for turn in history:
            for call in turn.get("tool_calls", ()):
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": call["call_id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                    }
                )
            for output in turn.get("outputs", ()):
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": output["call_id"],
                        "output": output["output"],
                    }
                )
        payload: dict[str, Any] = {
            "model": config.model,
            "input": input_items,
            "max_output_tokens": config.max_output_tokens,
        }
        if tools:
            payload["tools"] = list(tools)
        if config.reasoning_effort:
            payload["reasoning"] = {"effort": config.reasoning_effort}
        return payload

    @staticmethod
    def _chat_payload(
        config: TangtangConfig,
        persona: str,
        prompt: str,
        *,
        tools: tuple[dict[str, Any], ...] = (),
        history: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": persona},
            {"role": "user", "content": prompt},
        ]
        for turn in history:
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call["call_id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in turn.get("tool_calls", ())
                    ],
                }
            )
            for output in turn.get("outputs", ()):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": output["call_id"],
                        "content": output["output"],
                    }
                )
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_completion_tokens": config.max_output_tokens,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}}
                for tool in tools
            ]
        if config.reasoning_effort == "none":
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = config.reasoning_effort
        return payload

    @staticmethod
    def _extract_text(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        direct = data.get("output_text")
        if isinstance(direct, str):
            return direct
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "".join(
                        str(part.get("text", ""))
                        for part in content
                        if isinstance(part, dict) and part.get("type") != "reasoning_text"
                    )
        output = data.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "reasoning":
                    continue
                for content in item.get("content", []):
                    if (
                        isinstance(content, dict)
                        and content.get("type") != "reasoning_text"
                        and isinstance(content.get("text"), str)
                    ):
                        parts.append(content["text"])
            return "".join(parts)
        return ""

    @staticmethod
    def _extract_tool_calls(data: Any) -> tuple[dict[str, str], ...]:
        if not isinstance(data, dict):
            return ()
        calls: list[dict[str, str]] = []
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if not isinstance(function, dict):
                        continue
                    calls.append(
                        {
                            "call_id": str(call.get("id") or ""),
                            "name": str(function.get("name") or ""),
                            "arguments": str(function.get("arguments") or ""),
                        }
                    )
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                calls.append(
                    {
                        "call_id": str(item.get("call_id") or item.get("id") or ""),
                        "name": str(item.get("name") or ""),
                        "arguments": str(item.get("arguments") or ""),
                    }
                )
        return tuple(calls)

    @staticmethod
    def _unsupported_tools_error(response: httpx.Response) -> bool:
        try:
            body = (response.text or "").lower()
        except Exception:
            body = ""
        return response.status_code in {400, 404, 422} and "tool" in body

    @staticmethod
    def _extract_usage(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return {}
        result: dict[str, Any] = {}
        result["prompt_tokens"] = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        result["completion_tokens"] = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        details = usage.get("output_tokens_details")
        if isinstance(details, dict):
            result["reasoning_tokens"] = int(details.get("reasoning_tokens") or 0)
        else:
            result["reasoning_tokens"] = int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            )
        result["total_tokens"] = int(usage.get("total_tokens") or 0)
        return result


FeatureRouter = Callable[
    [Any, Any, "TangtangConfig", str], Awaitable[tuple[bool, dict[str, Any]]]
]


class TangtangService:
    def __init__(
        self,
        loader: TangtangConfigLoader | None = None,
        db: TangtangDb | None = None,
        provider: TangtangProvider | None = None,
        resource_dir: Path | None = None,
        usage_dir: Path | None = None,
        feature_router: FeatureRouter | None = None,
    ) -> None:
        self.loader = loader or TangtangConfigLoader()
        self.db = db or TangtangDb()
        self.provider = provider or TangtangProvider()
        self.feature_router = feature_router
        base = resource_dir or RESOURCE_DIR
        self._persona = _TextFileCache(base / "persona.md")
        self._hard = _TextFileCache(base / "hard_blacklist.txt")
        self._soft = _TextFileCache(base / "soft_blacklist.txt")
        self._lines = _TextFileCache(base / "lines.txt")
        self.usage_dir = usage_dir or USAGE_DIR
        self._group_context: dict[int, deque[str]] = {}
        self._last_canned: dict[int, str] = {}
        self._recent_call_texts: dict[tuple[int, str], float] = {}
        self._in_flight: set[int] = set()

    def config(self) -> TangtangConfig:
        return self.loader.load()

    def record_group_message(
        self,
        group_id: int,
        nickname: str,
        text: str,
        *,
        user_id: int = 0,
        message_id: str | int = "",
        created_at: str | None = None,
    ) -> None:
        queue = self._group_context.setdefault(
            int(group_id), deque(maxlen=GROUP_CONTEXT_MESSAGES)
        )
        queue.append(f"{nickname}: {text}")
        try:
            self.db.insert_group_message(
                group_id=int(group_id),
                user_id=int(user_id),
                nickname=nickname,
                text=text,
                message_id=message_id,
                created_at=created_at or self._now(),
            )
        except Exception as exc:
            logger.warning("Tangtang group message persist failed: {}", exc)

    def _group_context_lines(
        self, group_id: int, limit: int = GROUP_CONTEXT_MESSAGES
    ) -> list[str]:
        """Persisted recent group messages, falling back to the memory queue."""

        try:
            rows = self.db.recent_group_messages(group_id, limit)
        except Exception as exc:
            logger.warning("Tangtang group context read failed: {}", exc)
            rows = []
        if rows:
            return [f"{row['nickname'] or '群友'}: {row['text']}" for row in rows]
        return list(self._group_context.get(group_id, ()))

    def _user_history_lines(
        self,
        user_id: int,
        group_id: int,
        limit: int,
        max_chars: int,
    ) -> str:
        """A single user's own recent messages as persistent chat history."""

        try:
            rows = self.db.recent_user_messages(user_id, group_id, limit)
        except Exception as exc:
            logger.warning("Tangtang user history read failed: {}", exc)
            return ""
        if not rows:
            return ""
        lines = [f"{row['nickname'] or '群友'}：{row['text']}" for row in rows]
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return "…" + text[-max_chars:]

    def _persona_text(self) -> str:
        return self._persona.text() or DEFAULT_PERSONA

    def _hard_terms(self) -> frozenset[str]:
        return _term_set(self._hard.text())

    def _soft_terms(self) -> frozenset[str]:
        return _term_set(self._soft.text())

    def _hard_patterns(self) -> tuple[re.Pattern[str], ...]:
        return _regex_patterns(self._hard.text())

    def _soft_patterns(self) -> tuple[re.Pattern[str], ...]:
        return _regex_patterns(self._soft.text())

    def _pick_canned(self, group_id: int) -> str | None:
        lines = _line_list(self._lines.text())
        if not lines:
            return None
        previous = self._last_canned.get(int(group_id))
        choices = [line for line in lines if line != previous] or list(lines)
        chosen = random.choice(choices)
        self._last_canned[int(group_id)] = chosen
        return chosen

    @staticmethod
    def _normalize_call_text(text: str) -> str:
        return " ".join(str(text).split())

    def _claim_call_text(self, group_id: int, text: str) -> bool:
        """Reserve one normalized called message per group for one minute."""

        key = (int(group_id), self._normalize_call_text(text))
        now = time.monotonic()
        previous = self._recent_call_texts.get(key)
        if previous is not None and now - previous < CALL_REPEAT_MERGE_SECONDS:
            return False
        self._recent_call_texts[key] = now
        expired_before = now - CALL_REPEAT_MERGE_SECONDS
        self._recent_call_texts = {
            item: timestamp
            for item, timestamp in self._recent_call_texts.items()
            if timestamp >= expired_before
        }
        return True

    def _is_bot_reply_echo(self, group_id: int, text: str) -> bool:
        normalized = self._normalize_call_text(text)
        if not normalized:
            return False
        try:
            return self.db.has_recent_group_reply_text(
                int(group_id), str(text).strip(), limit=10
            )
        except Exception as exc:
            logger.warning("Tangtang reply echo lookup failed: {}", exc)
            return False

    def _now(self) -> str:
        return datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds")

    def _write_usage(
        self,
        config: TangtangConfig,
        group_id: int,
        user_id: int,
        event_kind: str,
        *,
        mode: str | None,
        tokens: dict[str, Any],
        detail: str = "",
    ) -> None:
        try:
            now = datetime.now(ZoneInfo(settings.timezone))
            path = self.usage_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": now.isoformat(timespec="seconds"),
                "group_id": int(group_id),
                "user_id": int(user_id),
                "event": event_kind,
                "mode": mode or "",
                "model": config.model if config.enabled else "",
                "latency_ms": tokens.get("latency_ms"),
                "prompt_tokens": int(tokens.get("prompt_tokens") or 0),
                "completion_tokens": int(tokens.get("completion_tokens") or 0),
                "reasoning_tokens": int(tokens.get("reasoning_tokens") or 0),
                "total_tokens": int(tokens.get("total_tokens") or 0),
                "detail": detail,
            }
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("Tangtang usage log write failed")

    def record_feature(
        self,
        *,
        group_id: int,
        user_id: int,
        message_id: str | int,
        call_text: str,
        reply_text: str,
    ) -> None:
        try:
            self.db.insert_call(
                group_id=group_id,
                user_id=user_id,
                message_id=message_id,
                call_text=call_text,
                reply_text=reply_text,
                reply_kind="feature",
                mode="feature",
                created_at=self._now(),
            )
        except Exception as exc:
            logger.warning("Tangtang feature history record failed: {}", exc)

    def _build_prompt(
        self,
        event: Any,
        config: TangtangConfig,
        *,
        at_labels: Mapping[str, str] | None = None,
        call_text: str | None = None,
        proactive: bool = False,
        black_meme_instruction: str | None = None,
    ) -> str:
        group_id = int(event.group_id)
        context_lines = self._group_context_lines(group_id, config.group_context_messages)
        history = self.db.model_reply_lines(
            int(event.user_id),
            group_id,
            config.history_messages,
            config.history_chars,
        )
        user_history = self._user_history_lines(
            int(event.user_id),
            group_id,
            config.history_messages,
            config.history_chars,
        )
        sender = getattr(event, "sender", None)
        nickname = str(getattr(sender, "nickname", "") or getattr(sender, "card", "") or "群友")
        mentioned = bool(event.is_tome())
        media_summary = self._media_summary(event)
        reply_text = ""
        reply = getattr(event, "reply", None)
        if reply is not None:
            try:
                reply_text = render_message_text(reply.message, at_labels)
            except Exception:
                reply_text = ""
        call_text = call_text if call_text is not None else render_message_text(
            event.message, at_labels
        )
        header = (
            "你在群里看到一条群友发言，没有人呼叫你。判断这条发言值不值得主动接话，"
            "再按糖糖人格决定。"
            if proactive
            else "你收到一条群友的呼叫消息。先判断这条消息值不值得接话，再按糖糖人格决定。"
        )
        section_label = "[当前群友发言]" if proactive else "[当前呼叫]"
        fixed_parts = [
            header,
            "",
            section_label,
            f"说话人昵称：{nickname}",
            f"被@状态：{'否' if proactive else ('是' if mentioned else '否')}",
            f"消息媒体：{media_summary}",
            f"引用消息：{reply_text if reply_text else '无'}",
            f"消息：{call_text}",
            "",
        ]
        if config.tools_enabled:
            fixed_parts.append(
                "如果回答需要查枝江或鸣潮的本地资料，可以调用查询工具获取，不要编造；"
                "资料没覆盖就明说不知道。"
            )
            fixed_parts.append("")
        if black_meme_instruction:
            fixed_parts.append(black_meme_instruction)
            fixed_parts.append("")
        if proactive:
            fixed_parts.append(
                "没有人呼叫糖糖，优先保持[沉默]；只有话题真的值得接话时才[接话]。"
            )
            fixed_parts.append("")
        fixed_parts.append(
            "输出格式：第一行必须是 [接话] 或 [沉默]，不要输出任何分析、理由或思考过程；"
            "若 [接话]，第二行起直接写糖糖的回复（1-3 句短句，不要解释你的判断）。"
        )
        fixed_text = "\n".join(fixed_parts)
        if len(fixed_text) >= config.max_input_chars:
            # The current call and the format instruction always stay intact,
            # even if the configured cap is impossibly small.
            return fixed_text
        context_parts = [
            f"[最近群聊气氛（最近 {config.group_context_messages} 条，仅供感受氛围，不要逐条复述）]",
            "\n".join(context_lines) if context_lines else "（暂无）",
            "",
            f"[你与该群友的成功互动历史（{config.history_messages} 条内，仅供参考延续）]",
            history or "（暂无）",
            "",
            f"[该群友最近发言（{config.history_messages} 条内，来自其历史聊天记录）]",
            user_history or "（暂无）",
        ]
        context_joined = "\n".join(context_parts)
        knowledge = self._local_knowledge(call_text)
        budget = config.max_input_chars - len(fixed_text) - 2
        if budget < 2:
            return fixed_text
        sections = [part for part in (knowledge, context_joined) if part]
        joined = "\n\n".join(sections)
        if len(joined) > budget:
            if knowledge:
                # 本地知识优先保留；空间不足时先丢弃群聊气氛与互动历史。
                joined = knowledge
                if len(joined) > budget:
                    joined = joined[: budget - 1] + "…"
                elif len(context_joined) > 3:
                    remaining = budget - len(joined)
                    if remaining > 3:
                        joined += "\n\n" + "…" + context_joined[-(remaining - 1) :]
            else:
                joined = "…" + context_joined[-(budget - 1) :]
        return joined + "\n\n" + fixed_text

    def _local_knowledge(self, text: str) -> str:
        query = text.strip()
        if not query:
            return ""
        sections: list[str] = []
        zhijiang_block = self._knowledge_section(
            query,
            "[本地枝江知识（来自本地百科数据库，回答相关问题时优先采用；资料没覆盖就明说不知道）]",
            ZHIJIANG_KNOWLEDGE_LIMIT,
            ZHIJIANG_KNOWLEDGE_MAX_CHARS,
            zhijiang_search,
            "zhijiang",
        )
        mingchao_block = self._knowledge_section(
            query,
            "[本地鸣潮梗文化（来自本地梗文化数据库，回答相关问题时优先采用；资料没覆盖就明说不知道）]",
            MINGCHAO_MEME_LIMIT,
            MINGCHAO_MEME_MAX_CHARS,
            mingchao_meme_search,
            "mingchao",
        )
        for block in (zhijiang_block, mingchao_block):
            if block:
                sections.append(block)
        if not sections:
            return ""
        joined = "\n\n".join(sections)
        if len(joined) > LOCAL_KNOWLEDGE_MAX_CHARS:
            joined = joined[: LOCAL_KNOWLEDGE_MAX_CHARS - 1] + "…"
        return joined

    def _knowledge_section(
        self,
        query: str,
        header: str,
        limit: int,
        max_chars: int,
        searcher: Callable[[str, int], tuple[Any, ...]],
        source_name: str,
    ) -> str:
        try:
            matches = searcher(query, limit)
        except Exception as exc:
            logger.warning("Tangtang local {} knowledge lookup failed: {}", source_name, exc)
            return ""
        if not matches:
            return ""
        lines = [header]
        used = 0
        for entry in matches:
            related_hint = ""
            if getattr(entry, "related", ()):
                related_hint = "（相关：" + "、".join(
                    title for _ref, title in entry.related
                ) + "）"
            line = f"· {entry.title}：{entry.summary}{related_hint}"
            if used and used + len(line) + 1 > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    @staticmethod
    def _media_summary(event: Any) -> str:
        labels: list[str] = []
        for segment in getattr(event, "message", ()):
            seg_type = str(getattr(segment, "type", "") or "")
            if seg_type in {"text", "at", "reply"}:
                continue
            label = {
                "image": "图片",
                "face": "表情",
                "record": "语音",
                "voice": "语音",
                "video": "视频",
                "file": "文件",
            }.get(seg_type, "其他")
            if label not in labels:
                labels.append(label)
        return "、".join(labels) if labels else "纯文本"

    async def handle(self, bot: Any, event: Any, config: TangtangConfig) -> None:
        group_id = int(event.group_id)
        user_id = int(event.user_id)
        text = event.get_plaintext().strip()
        if not text and not bool(event.is_tome()):
            return
        if self._is_bot_reply_echo(group_id, text):
            self._write_usage(config, group_id, user_id, "bot_reply_echo", mode=None, tokens={})
            return
        if not self._claim_call_text(group_id, text):
            self._write_usage(config, group_id, user_id, "call_repeat", mode=None, tokens={})
            return
        at_labels = await resolve_at_labels(bot, event)
        call_text = render_message_text(event.message, at_labels) or text
        lowered = text.lower()
        if _matches(self._hard_terms(), self._hard_patterns(), text):
            self._write_usage(config, group_id, user_id, "hard_block", mode=None, tokens={})
            return
        black_meme_instruction = _black_meme_instruction(text)
        if black_meme_instruction:
            await self._model_reply(
                bot,
                event,
                config,
                at_labels=at_labels,
                call_text=call_text,
                black_meme_instruction=black_meme_instruction,
                force_reply=config.requires_call_reply(group_id),
            )
            return
        if self.feature_router is not None:
            handled, tokens = await self.feature_router(bot, event, config, text)
            self._write_usage(
                config,
                group_id,
                user_id,
                "feature" if handled else "feature_router",
                mode=config.mode,
                tokens=tokens,
            )
            if handled:
                return
        if (
            config.call_ignore_probability_for(group_id) > 0
            and random.random() < config.call_ignore_probability_for(group_id)
        ):
            self._write_usage(config, group_id, user_id, "skip", mode=None, tokens={})
            return
        if _matches(self._soft_terms(), self._soft_patterns(), text):
            if (
                config.soft_blacklist_ignore_probability > 0
                and random.random() < config.soft_blacklist_ignore_probability
            ):
                self._write_usage(
                    config, group_id, user_id, "soft_blacklist_skip", mode=None, tokens={}
                )
                return
            line = self._pick_canned(group_id)
            if line:
                await self._send_and_record(
                    bot, event, config, line, reply_kind="canned", mode="local",
                    tokens={}, call_text=call_text,
                )
            return
        if text == config.call_keyword or text == "":
            line = self._pick_canned(group_id)
            if line:
                await self._send_and_record(
                    bot, event, config, line, reply_kind="canned", mode="local",
                    tokens={}, call_text=call_text,
                )
            return
        if config.mode == "c":
            decision = classify_call(text)
            if decision == "question":
                await self._model_reply(
                    bot,
                    event,
                    config,
                    at_labels=at_labels,
                    call_text=call_text,
                    force_reply=config.requires_call_reply(group_id),
                )
            elif random.random() < config.c_probability:
                await self._model_reply(
                    bot,
                    event,
                    config,
                    at_labels=at_labels,
                    call_text=call_text,
                    force_reply=config.requires_call_reply(group_id),
                )
            else:
                if config.requires_call_reply(group_id):
                    line = self._pick_canned(group_id) or "我在，怎么啦？"
                    await self._send_and_record(
                        bot,
                        event,
                        config,
                        line,
                        reply_kind="canned",
                        mode="local",
                        tokens={},
                        call_text=call_text,
                    )
                    return
                self._write_usage(
                    config, group_id, user_id, "skip", mode="c", tokens={}
                )
            return
        await self._model_reply(
            bot,
            event,
            config,
            at_labels=at_labels,
            call_text=call_text,
            force_reply=config.requires_call_reply(group_id),
        )

    async def handle_proactive(
        self,
        bot: Any,
        event: Any,
        config: TangtangConfig,
    ) -> None:
        """Decide whether to proactively reply to one ordinary group message."""

        group_id = int(event.group_id)
        user_id = int(event.user_id)
        text = event.get_plaintext().strip()
        if (
            not config.enabled
            or not config.proactive_enabled
            or not text
            or group_id not in config.group_ids
        ):
            return
        if _black_meme_instruction(text):
            return
        hard_match = _match_detail(
            self._hard_terms(), self._hard_patterns(), text
        )
        if hard_match:
            self._write_usage(
                config, group_id, user_id, "proactive_hard_block",
                mode="proactive", tokens={}, detail=hard_match,
            )
            return
        soft_match = _match_detail(
            self._soft_terms(), self._soft_patterns(), text
        )
        if soft_match:
            self._write_usage(
                config, group_id, user_id, "proactive_skip",
                mode="proactive", tokens={}, detail=soft_match,
            )
            return
        probability, cooldown_seconds, message_interval = config.proactive_values_for(group_id)
        decision = self.db.claim_proactive_reply(
            group_id=group_id,
            now=time.time(),
            probability=probability,
            cooldown_seconds=cooldown_seconds,
            message_interval=message_interval,
            random_value=random.random(),
        )
        if decision != "claimed":
            return
        at_labels = await resolve_at_labels(bot, event, use_api=False)
        call_text = render_message_text(event.message, at_labels) or text
        await self._model_reply(
            bot,
            event,
            config,
            at_labels=at_labels,
            call_text=call_text,
            proactive=True,
        )

    async def _model_reply(
        self,
        bot: Any,
        event: Any,
        config: TangtangConfig,
        *,
        at_labels: Mapping[str, str] | None = None,
        call_text: str | None = None,
        proactive: bool = False,
        black_meme_instruction: str | None = None,
        force_reply: bool = False,
    ) -> None:
        group_id = int(event.group_id)
        user_id = int(event.user_id)
        if group_id in self._in_flight:
            self._write_usage(
                config,
                group_id,
                user_id,
                "in_flight",
                mode="proactive" if proactive else config.mode,
                tokens={},
            )
            return
        self._in_flight.add(group_id)
        mode = "proactive" if proactive else config.mode
        try:
            persona = self._persona_text()
            prompt = self._build_prompt(
                event,
                config,
                at_labels=at_labels,
                call_text=call_text,
                proactive=proactive,
                black_meme_instruction=black_meme_instruction,
            )
            tools = TOOL_SCHEMAS if config.tools_enabled else ()
            history: list[dict[str, Any]] = []
            result = await self.provider.generate_agent(
                config, persona, prompt, tools, tuple(history)
            )
            usage = dict(result.usage)
            loops = 0
            while result.tool_calls and loops < config.tool_loop_max:
                outputs = tuple(self._run_tool(call) for call in result.tool_calls)
                history.append({"tool_calls": result.tool_calls, "outputs": outputs})
                result = await self.provider.generate_agent(
                    config, persona, prompt, tools, tuple(history)
                )
                usage = self._merge_usage(usage, result.usage)
                loops += 1
            answer_raw = result.text
            decided, answer = parse_decision(answer_raw)
            if not decided or not answer:
                if force_reply:
                    line = self._pick_canned(group_id) or "我在，怎么啦？"
                    await self._send_and_record(
                        bot,
                        event,
                        config,
                        line,
                        reply_kind="canned",
                        mode="local",
                        tokens=usage,
                        call_text=call_text,
                    )
                    return
                self._write_usage(
                    config, group_id, user_id, "silent", mode=mode, tokens=usage
                )
                return
            if black_meme_instruction and any(
                term in answer for term in FORBIDDEN_LOCAL_TERMS
            ):
                answer = "不谈这个fifa人物"
            await self._send_and_record(
                bot,
                event,
                config,
                answer,
                reply_kind="proactive" if proactive else "model",
                mode=mode,
                tokens=usage,
                call_text=call_text,
                proactive=proactive,
            )
        except Exception as exc:
            logger.warning(
                "Tangtang model request failed; response suppressed: "
                f"{type(exc).__name__}: {exc}"
            )
            self._write_usage(
                config, group_id, user_id, "error", mode=mode, tokens={}
            )
        finally:
            self._in_flight.discard(group_id)

    def _run_tool(self, call: Mapping[str, str]) -> dict[str, str]:
        result = self._execute_tool(call)
        text = json.dumps(result, ensure_ascii=False)
        if len(text) > TOOL_MAX_RESULT_CHARS:
            text = text[: TOOL_MAX_RESULT_CHARS - 1] + "…"
        return {"call_id": str(call.get("call_id") or ""), "output": text}

    def _execute_tool(self, call: Mapping[str, str]) -> dict[str, Any]:
        name = str(call.get("name") or "")
        raw_arguments = str(call.get("arguments") or "")
        try:
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            if not isinstance(arguments, dict):
                arguments = {}
        except (ValueError, TypeError):
            arguments = {}
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}
        if name == "search_zhijiang_knowledge":
            matches = zhijiang_search(query, ZHIJIANG_KNOWLEDGE_LIMIT)
        elif name == "search_mingchao_meme_culture":
            matches = mingchao_meme_search(query, MINGCHAO_MEME_LIMIT)
        else:
            return {"error": f"unknown tool: {name}"}
        results = [entry for entry in (_safe_tool_entry(entry) for entry in matches) if entry]
        return {"results": results}

    @staticmethod
    def _merge_usage(accumulated: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        merged = dict(accumulated)
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens", "latency_ms"):
            merged[key] = int(merged.get(key) or 0) + int(current.get(key) or 0)
        return merged

    async def _send_and_record(
        self,
        bot: Any,
        event: Any,
        config: TangtangConfig,
        answer: str,
        *,
        reply_kind: str,
        mode: str,
        tokens: dict[str, Any],
        call_text: str | None = None,
        proactive: bool = False,
    ) -> None:
        group_id = int(event.group_id)
        user_id = int(event.user_id)
        try:
            await call_qq_action(
                bot,
                "send_group_msg",
                group_id=group_id,
                message=(
                    answer
                    if proactive
                    else quote_message(event, answer)
                ),
            )
        except Exception as exc:
            logger.warning(
                "Tangtang reply send failed (group_id={}, message_id={}): {}",
                group_id,
                event.message_id,
                exc,
            )
            self._write_usage(config, group_id, user_id, "error", mode=mode, tokens=tokens)
            return
        try:
            self.db.insert_call(
                group_id=group_id,
                user_id=user_id,
                message_id=str(getattr(event, "message_id", "") or ""),
                call_text=(
                    call_text
                    if call_text is not None
                    else event.get_plaintext().strip()
                ),
                reply_text=answer,
                reply_kind=reply_kind,
                mode=mode,
                created_at=self._now(),
            )
        except Exception as exc:
            logger.warning("Tangtang history record failed: {}", exc)
        self._write_usage(
            config,
            group_id,
            user_id,
            (
                "proactive_reply"
                if reply_kind == "proactive"
                else ("reply" if reply_kind == "model" else "canned")
            ),
            mode=mode,
            tokens=tokens,
        )
