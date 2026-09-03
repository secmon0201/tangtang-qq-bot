"""Independent GsUID game-interface gate and controls.

The upstream GenshinUID Core connector forwards every group message to the
local Core process. This module is the only place that decides whether a game
command is recognized:

- the ``nte`` and ``ww`` prefixes are recognized with or without ``#``;
- bare game prefixes other than those two (``gs``/``ys``/``yh``/``gsuid``)
  are never recognized;
- the game interface has its own hot switch and group scope, independent from
  the mini-game feature scope and the live-guard pause switch;
- game commands are group-only and are subject only to the global
  managed-group boundary plus this module's own settings.
"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor

from bot.config import settings
from bot.services.game_api_gate import game_message_disposition, game_prefix
from bot.services.runtime import group_domains, passive_settings


@event_preprocessor
async def _(event: MessageEvent):
    text = event.get_plaintext().strip()
    store = passive_settings()
    prefix = game_prefix(text)
    if isinstance(event, GroupMessageEvent):
        feature_enabled = bool(
            prefix and group_domains().effective_feature_enabled(int(event.group_id), prefix)
        )
        disposition = game_message_disposition(
            text,
            is_group=True,
            master_enabled=settings.gsuid_enabled,
            hot_enabled=store.is_game_api_enabled(),
            in_scope=feature_enabled,
        )
    else:
        disposition = game_message_disposition(
            text,
            is_group=False,
            master_enabled=False,
            hot_enabled=False,
            in_scope=False,
        )
    if disposition != "pass":
        raise IgnoredException(f"game interface gate: {disposition} (text={text!r})")
