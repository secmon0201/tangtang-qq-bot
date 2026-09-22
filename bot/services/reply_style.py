"""Pure, content-free observations of repeated conversational expression."""

from __future__ import annotations

import re
from collections.abc import Iterable


_PATTERNS = {
    "wu_opening": re.compile(r"^\s*唔"),
    "dash": re.compile(r"——"),
    "ellipsis": re.compile(r"…|\.{3}"),
    "acceptance": re.compile(r"收下"),
    "dismissal": re.compile(r"当.{0,6}没(?:看|听)(?:到|见)"),
}
_REMINDERS = (
    ("wu_opening", 3, "直接从内容起句，不重复语气词开场"),
    ("dash", 2, "不重复用破折号制造停顿"),
    ("ellipsis", 3, "不重复用省略号拖顿"),
    ("acceptance", 2, "具体回应对方，不套用收下式回应"),
    ("dismissal", 2, "直接表达态度，不套用假装没看见或没听见式回应"),
)


def expression_counts(replies: Iterable[str]) -> dict[str, int]:
    """Count turns containing each expression; categories can overlap."""
    counts = dict.fromkeys(("reply_count", *_PATTERNS), 0)
    for text in replies:
        counts["reply_count"] += 1
        for key, pattern in _PATTERNS.items():
            counts[key] += int(pattern.search(text) is not None)
    return counts


def repetition_reminder(replies: Iterable[str]) -> str:
    """Return bounded fixed labels only, never any source text or identities."""
    counts = expression_counts(replies)
    reminders = [label for key, threshold, label in _REMINDERS if counts[key] >= threshold]
    if not reminders:
        return ""
    return ("[本轮表达提醒]\n近期有重复表达：" + "；".join(reminders)
            + "。保持自然，必要的犹豫、引用和真实接受或拒绝不受限。")[:200]
