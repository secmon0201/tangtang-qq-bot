"""Pure gate rules for the independent NTEUID game interface.

Kept free of NoneBot imports so the matching rules can be unit-tested without
initializing a driver. ``bot.plugins.game_api`` turns these rules into an
event preprocessor and the operator-facing management commands.
"""

from __future__ import annotations

import re


GAME_API_PREFIX = "nte"

# Bare game-shaped messages for disabled plugins are still dropped. Bare NTE
# is handled separately so the compatibility prefix can reach NTEUID.
BARE_GAME_COMMAND_RE = re.compile(
    r"^(?:gs|ys|ww|nte|yh|gsuid)(?=$|[\s\u4e00-\u9fff]|[^\w])",
    re.IGNORECASE,
)
BARE_NTE_COMMAND_RE = re.compile(
    r"^nte(?=$|[\s\u4e00-\u9fff]|bot|群)",
    re.IGNORECASE,
)
# A # command with a game prefix other than nte stays silent so that #nte is
# the only recognized game-interface entry point.
HASH_OTHER_GAME_COMMAND_RE = re.compile(
    r"^#\s*(?:gs|ys|ww|yh|gsuid)(?=$|[\s\u4e00-\u9fff]|[^\w])",
    re.IGNORECASE,
)
HASH_GAME_COMMAND_RE = re.compile(
    rf"^#\s*{re.escape(GAME_API_PREFIX)}(?=$|[\s\u4e00-\u9fff]|[^\w])",
    re.IGNORECASE,
)
NTE_GAME_COMMAND_RE = re.compile(
    r"^#?\s*nte(?=$|[\s\u4e00-\u9fff]|bot|群)",
    re.IGNORECASE,
)


def game_message_disposition(
    text: str,
    *,
    is_group: bool,
    master_enabled: bool,
    hot_enabled: bool,
    in_scope: bool,
) -> str:
    """Classify one plaintext message for the independent game gate.

    Returns ``pass`` only when the upstream connector may see the message;
    otherwise returns a short reason. Every non-pass game-shaped message is
    silently dropped by the plugin preprocessor.
    """
    if BARE_GAME_COMMAND_RE.match(text) and not BARE_NTE_COMMAND_RE.match(text):
        return "no-hash-prefix"
    if HASH_OTHER_GAME_COMMAND_RE.match(text):
        return "disabled-prefix"
    if not NTE_GAME_COMMAND_RE.match(text):
        return "pass"
    if not is_group:
        return "private-chat"
    if not master_enabled or not hot_enabled:
        return "disabled"
    if not in_scope:
        return "out-of-scope"
    return "pass"
