"""Deterministic cleanup of AI-flavored chatter for Tangtang replies.

The Chinese baseline is op7418/Humanizer-zh
(MIT License, https://github.com/op7418/Humanizer-zh), which itself translates
blader/humanizer (MIT License, https://github.com/blader/humanizer). Both are
based on Wikipedia's "Signs of AI writing" maintained by WikiProject AI Cleanup.

Only the safe, reversible subset is ported: delete chatbot closers, praise
openers, fake-candid hooks, unsourced attributions and stacked qualifiers, and
collapse punctuation runs. Nothing here rewrites meaning or invents content.

Deliberately NOT ported: the library's "passive voice / missing subjects"
rule, because Tangtang's house style is the opposite. She omits subjects like
a normal group member (see bot/resources/tangtang/persona.md).
"""

from __future__ import annotations

import re

_PUNCT_RUNS = re.compile(r"([!！?？。~～…])\1+")

_START_HOOKS = re.compile(
    r"^(?:"
    r"(?:说实话|老实说|讲真|说真的|坦白讲)[，,。！!~～\s]+|"
    r"(?:好问题|问得好|你说得太对了|完全正确)[！!，,。\s]*|"
    r"(?:众所周知|有研究表明|专家表示|据我所知|以我目前所知|"
    r"根据目前公开的信息|基于可用信息|此外|值得注意的是|值得一提的是|"
    r"需要注意的是|总而言之|综上所述|总的来说|说到底|归根结底|"
    r"本质上来说)[，,:：。\s]*"
    r")"
)

_QUALIFIER_RUN = re.compile(r"(?:(?:可能|大概|也许|或许|应该)[，,、\s]?){2,}")
_FIRST_QUALIFIER = re.compile(r"可能|大概|也许|或许|应该")

_TRAILING_CLOSERS = (
    re.compile(
        r"[，,\s]*(?:希望(?:以上|这些|这条)?(?:信息|内容|回复)?"
        r"(?:对(?:你|您|大家))?(?:能|能够|会)?(?:有(?:所)?|起到|带来)?帮助|"
        r"希望(?:能|可以)?(?:帮到|帮助)(?:你|您))[。！!~～\s]*$"
    ),
    re.compile(
        r"[，,\s]*(?:如果|如)(?:还有|有)(?:其他|其它)?(?:问题|疑问|需要)"
        r"[^。！!？?]{0,14}(?:随时|可以)(?:问|找|联系)(?:我|糖糖)[。！!~～\s]*$"
    ),
    re.compile(
        r"[，,\s]*(?:很高兴|很乐意)(?:能|能够)?(?:帮到|帮助|解答)"
        r"(?:你|您)[。！!~～\s]*$"
    ),
    re.compile(
        r"[，,\s]*(?:请|欢迎)(?:随时)?(?:告诉|问|找|联系)(?:我|糖糖)"
        r"[。！!~～\s]*$"
    ),
)


def _first_qualifier(match: re.Match[str]) -> str:
    found = _FIRST_QUALIFIER.search(match.group(0))
    return found.group(0) if found else match.group(0)


def humanize_text(text: str) -> str:
    """Clean one reply message without changing what it says."""

    value = _PUNCT_RUNS.sub(r"\1", text.strip())
    value = _START_HOOKS.sub("", value).lstrip()
    for _ in range(4):
        updated = value
        for pattern in _TRAILING_CLOSERS:
            updated = pattern.sub("", updated)
        updated = updated.strip()
        if updated == value:
            break
        value = updated
    return _QUALIFIER_RUN.sub(_first_qualifier, value)


def humanize_messages(messages: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Clean a planned reply bubble-by-bubble and drop emptied bubbles."""

    return tuple(
        cleaned for item in messages if (cleaned := humanize_text(str(item)))
    )


__all__ = ["humanize_messages", "humanize_text"]
