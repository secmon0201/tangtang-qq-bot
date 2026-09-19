from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from bot.services.local_skill_contract import FeatureRequest as SkillCall, parse_skill_call, MAX_SKILL_CALLS


MESSAGE_MARKER = "[消息]"
_DETAIL_REQUEST_RE = re.compile(
    r"详细|展开|长篇|完整|全面|深入|逐步|一步一步|教程|分析|梳理|总结|"
    r"讲清楚|解释清楚|具体讲|仔细说|多说点"
)
_CONTROL_MARKER = re.compile(r"\[CQ:|\[(?:消息|接话|沉默)\]|</?(?:analysis|think)>|\"(?:decision|text_fallback)\"\s*:")


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    decided: bool
    messages: tuple[str, ...]
    voice: str = "auto"
    text_fallback: tuple[str, ...] = ()
    expression: str = ""
    structured: bool = False
    expression_candidates: tuple[str, ...] = ()
    memory_updates: tuple[dict, ...] = ()
    impression_updates: tuple[dict, ...] = ()
    growth_updates: tuple[dict, ...] = ()
    skill_calls: tuple[SkillCall, ...] = ()
    invalid_skill_request: bool = False

    @property
    def skill_call(self) -> SkillCall | None:
        return self.skill_calls[0] if self.skill_calls else None

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


def reply_style_instruction(text: str, random_value: float | None = None) -> str:
    """Choose a short-chat style, allowing long replies only when requested."""

    if _DETAIL_REQUEST_RE.search(text):
        return (
            "对方明确要求详细说明，可以按需要写完整；先尝试用 1-2 条说清，"
            "只有信息确实很多时才用 3 条以上。删掉铺垫、重复和总结套话。"
        )
    roll = random.random() if random_value is None else float(random_value)
    if roll < 0.35:
        return (
            "本次只发一条极短消息，2-8 字就够；能用残句就别补成完整句，"
            "句尾通常不加句号。"
        )
    if roll < 0.90:
        return (
            "本次只发一条日常短消息，尽量控制在 4-12 字；"
            "只有一个意思确实装不下才加第二条，总长尽量不超过 20 字。"
        )
    return (
        "本次可以稍多说一点，仍优先只发一条；确有两个意思时才发两条，"
        "单条超过 20 字就先删掉不必要内容。"
    )


def reply_bubble_limit(text: str, configured_max: int = 6) -> int:
    """Keep ordinary chat to two bubbles while preserving explicit detail requests."""

    limit = max(1, int(configured_max))
    return limit if _DETAIL_REQUEST_RE.search(text) else min(limit, 2)


def parse_reply_plan(
    text: str,
    *,
    max_bubbles: int = 6,
    max_chars: int = 1200,
) -> ReplyPlan:
    stripped = text.strip()
    if not stripped:
        return ReplyPlan(False, ())
    last_reply = stripped.rfind("[接话]")
    last_silent = stripped.rfind("[沉默]")
    if last_silent >= 0 and last_silent > last_reply:
        return ReplyPlan(False, ())
    if last_reply >= 0:
        body = stripped[last_reply + len("[接话]") :].strip()
    elif "沉默" in stripped[:8]:
        return ReplyPlan(False, ())
    else:
        body = stripped
    if not body:
        return ReplyPlan(False, ())

    payload = _reply_object(body)
    calls = ()
    if 'feature_call' in payload or 'feature_calls' in payload:
        if payload.get('decision') != 'reply' or ('feature_call' in payload and 'feature_calls' in payload):
            return ReplyPlan(False, (), invalid_skill_request=payload.get('decision') == 'reply')
        values = payload.get('feature_calls', [payload.get('feature_call')])
        if not isinstance(values, list) or len(values) > MAX_SKILL_CALLS:
            return ReplyPlan(False, (), invalid_skill_request=True)
        calls = tuple(parse_skill_call(value) for value in values)
        if any(call is None for call in calls) or len(set(calls)) != len(calls):
            return ReplyPlan(False, (), invalid_skill_request=True)
        if not isinstance(payload.get('messages'), list) or any(not isinstance(m, str) for m in payload['messages']):
            return ReplyPlan(False, (), invalid_skill_request=True)
    json_messages = _json_messages(body)
    if json_messages is not None:
        if not json_messages and not calls:
            return ReplyPlan(False, ())
        messages = json_messages
    else:
        messages = _marked_messages(body) or (body,)
    cleaned: list[str] = []
    remaining = max(1, int(max_chars))
    truncated = len(messages) > max(1, int(max_bubbles))
    for message in messages[: max(1, int(max_bubbles))]:
        value = str(message).strip()
        if not value:
            continue
        if len(value) > remaining:
            truncated = True
            value = "…" if remaining == 1 else value[: remaining - 1].rstrip() + "…"
        cleaned.append(value)
        remaining -= len(value)
        if remaining <= 0:
            break
    payload = _reply_object(body)
    voice = str(payload.get("voice", "auto")) if payload else "auto"
    fallback = payload.get("text_fallback", []) if payload else []
    valid = bool(payload) and payload.get("decision") == "reply" and voice in {"auto", "accept", "decline", "text"}
    if not isinstance(fallback, list) or any(not isinstance(v, str) for v in fallback):
        valid = False
        fallback = []
    if any(_CONTROL_MARKER.search(v) for v in (*cleaned, *fallback)):
        return ReplyPlan(False, ())
    if sum(len(v) for v in fallback) > max_chars or len(fallback) > max_bubbles:
        fallback = []
        voice = "text"
    if truncated:
        # Preserve the existing text length limit, but never speak a shortened
        # answer as though it were the complete final text.
        voice = "text"
    # Malformed control payloads must never be shown as a chat message.
    if json_messages is None and (body.startswith("{") or body.startswith("```json")):
        return ReplyPlan(False, ())
    candidates = payload.get("expression_candidates", []) if payload else []
    candidates = tuple(v for v in candidates[:3] if isinstance(v, str) and len(v) <= 64) if isinstance(candidates, list) else ()
    updates = payload.get("memory_updates", []) if payload else []
    updates = tuple(v for v in updates[:3] if isinstance(v, dict)) if isinstance(updates, list) else ()
    impressions = payload.get('impression_updates', []) if payload else []
    impressions = tuple(v for v in impressions[:2] if isinstance(v, dict)) if isinstance(impressions, list) else ()
    growth = payload.get('growth_updates', []) if payload else []
    growth = tuple(v for v in growth[:1] if isinstance(v, dict)) if isinstance(growth, list) else ()
    if calls and not valid:
        return ReplyPlan(False, (), invalid_skill_request=True)
    return ReplyPlan(bool(cleaned) or bool(calls), tuple(cleaned), voice if valid else "text",
                     tuple(v.strip() for v in fallback if v.strip())[:max_bubbles],
                     str(payload.get("expression", "")) if payload else "", valid, candidates, updates, impressions, growth,
                     calls)


def _reply_object(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        payload = json.loads(candidate)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_messages(body: str) -> tuple[str, ...] | None:
    candidate = body
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    if not candidate.startswith("{"):
        return None
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    decision = str(payload.get("decision") or "reply").lower()
    if decision in {"silent", "silence", "沉默"}:
        return ()
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    return tuple(str(item) for item in messages if isinstance(item, str) and item.strip())


def _marked_messages(body: str) -> tuple[str, ...]:
    if MESSAGE_MARKER not in body:
        return ()
    messages: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(MESSAGE_MARKER):
            if current:
                messages.append("\n".join(current).strip())
            current = [stripped[len(MESSAGE_MARKER) :].strip()]
        elif current:
            current.append(line.rstrip())
    if current:
        messages.append("\n".join(current).strip())
    return tuple(message for message in messages if message)


__all__ = [
    "MESSAGE_MARKER",
    "ReplyPlan",
    "SkillCall",
    "parse_reply_plan",
    "parse_skill_call",
    "reply_bubble_limit",
    "reply_style_instruction",
]
