from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


_IDIOM_RE = re.compile(r"^[\u4e00-\u9fff]{4}$")
_IDIOM_PATH = Path(__file__).resolve().parent.parent / "resources" / "idioms.txt"
_WORD_PATH = Path(__file__).resolve().parent.parent / "resources" / "four_character_words.txt"


@lru_cache(maxsize=1)
def idiom_words() -> frozenset[str]:
    """Return the bundled, offline four-character idiom vocabulary."""
    return frozenset(
        line.strip()
        for line in _IDIOM_PATH.read_text(encoding="utf-8").splitlines()
        if _IDIOM_RE.fullmatch(line.strip())
    )


def is_valid_idiom(value: str | None) -> bool:
    return bool(value and _IDIOM_RE.fullmatch(value) and value in idiom_words())


@lru_cache(maxsize=1)
def four_character_words() -> frozenset[str]:
    """Return the bundled offline four-character word vocabulary."""
    if not _WORD_PATH.exists():
        return frozenset()
    return frozenset(
        line.strip()
        for line in _WORD_PATH.read_text(encoding="utf-8").splitlines()
        if _IDIOM_RE.fullmatch(line.strip())
    )


def is_valid_four_character_word(value: str | None) -> bool:
    return bool(value and _IDIOM_RE.fullmatch(value) and value in four_character_words())
