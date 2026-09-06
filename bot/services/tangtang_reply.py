from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass


MESSAGE_MARKER = "[消息]"
_DETAIL_REQUEST_RE = re.compile(
    r"详细|展开|长篇|完整|全面|深入|逐步|一步一步|教程|分析|梳理|总结|为什么"
)


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    decided: bool
    messages: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


def reply_style_instruction(text: str, random_value: float | None = None) -> str:
    """Choose a length tier whose probability decreases as replies get longer."""

    if _DETAIL_REQUEST_RE.search(text):
        return (
            "这条消息明确需要解释，可以少见地使用长回答；仍以短句为主，只保留必要信息，"
            "按自然语义拆成 1-4 条消息，不要为了凑短而省略必要内容。"
        )
    roll = random.random() if random_value is None else float(random_value)
    if roll < 0.20:
        return "本次偏极短：用 1-2 条消息，多数句子 15 字上下，必要时可以有一条稍长。"
    if roll < 0.75:
        return (
            "本次偏短：用 2-4 条自然短消息，多数句子 15 字上下；偶尔允许一条必要的长句，"
            "让长短有起伏、避免每条等长；超过 16 字只作为倾向压低，不是禁令。"
        )
    if roll < 0.95:
        return (
            "本次可稍展开：用 1-3 条消息，长短交错；"
            "只有确实需要时才展开，别为长而长。"
        )
    return "本次允许少见的长回答：按必要长度写完整，不要无意义扩写。"


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

    json_messages = _json_messages(body)
    if json_messages is not None:
        if not json_messages:
            return ReplyPlan(False, ())
        messages = json_messages
    else:
        messages = _marked_messages(body) or (body,)
    cleaned: list[str] = []
    remaining = max(1, int(max_chars))
    for message in messages[: max(1, int(max_bubbles))]:
        value = str(message).strip()
        if not value:
            continue
        if len(value) > remaining:
            value = "…" if remaining == 1 else value[: remaining - 1].rstrip() + "…"
        cleaned.append(value)
        remaining -= len(value)
        if remaining <= 0:
            break
    return ReplyPlan(bool(cleaned), tuple(cleaned))


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


__all__ = ["MESSAGE_MARKER", "ReplyPlan", "parse_reply_plan", "reply_style_instruction"]
