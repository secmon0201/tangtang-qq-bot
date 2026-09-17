"""Explicit expression requests and the ordinary-expression sampling gate."""
from __future__ import annotations

import re


EXPRESSION_PROBABILITY = 0.60
INLINE_EXPRESSION_PROBABILITY = 0.5
_NOUN = r"(?:表情包|表情|贴纸)"
_NO_EXPRESSION = re.compile(r"(?:不要|别|不用|不必|禁止).{0,8}" + _NOUN)
_REQUEST = re.compile(
    r"(?:发|来|给我|想看|我要|我想要)(?:给我)?(?:一)?(?:个|张|些)?"
    r"(?:你的|微笑|大笑|思考|探头)?" + _NOUN +
    r"|" + _NOUN + r"[，, ]*(?:来一个|发一个|给我)"
)


def expression_request(text: str, names: tuple[str, ...] = ()) -> str:
    if _NO_EXPRESSION.search(text):
        return "none"
    return "explicit" if _REQUEST.search(text) or requested_name(text, names) is not None else "ordinary"


def requested_name(text: str, names: tuple[str, ...]) -> str | None:
    """Match a registered name inside a request, not elsewhere in the conversation."""
    if not names:
        return None
    alternatives = "|".join(re.escape(n) for n in sorted(set(names), key=len, reverse=True))
    match = re.search(r"(?:发|来|给我|想看|我要|我想要)(?:给我)?(?:一)?(?:个|张|些)?"
                      r"(?:你的)?(?P<name>" + alternatives + r")(?:的)?" + _NOUN, text)
    return match.group("name") if match else None


def expression_key(text: str, selected: str, roll: float) -> str:
    request = expression_request(text)
    if request == "none":
        return ""
    if request == "explicit":
        for label, key in (("大笑", "laugh"), ("思考", "think"), ("探头", "peek"), ("微笑", "smile")):
            if label in text:
                return key
        return selected or "smile"
    return selected if roll < EXPRESSION_PROBABILITY else ""
