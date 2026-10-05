"""The two upstream game command prefixes accepted by the QQ bridge."""
from __future__ import annotations

import re


_GAME_PREFIX = re.compile(r"^#?\s*(nte|ww)(?=$|[\s\u4e00-\u9fff]|bot)", re.IGNORECASE)


def game_prefix(text: str) -> str | None:
    match = _GAME_PREFIX.match(text.strip())
    return match.group(1).casefold() if match else None
