"""Independent GenshinUID/NTEUID game-interface gate and controls.

The upstream GenshinUID Core connector forwards every group message to the
local Core process. This module is the only place that decides whether a game
command is recognized:

- the ``nte`` prefix is recognized with or without ``#`` (case-insensitive);
- bare game prefixes other than ``nte`` (``gs``/``ys``/``ww``/``yh``/``gsuid``)
  are never recognized;
- the game interface has its own hot switch and group scope, independent from
  the mini-game feature scope and the live-guard pause switch;
- game commands are group-only and are subject only to the global
  managed-group boundary plus this module's own settings.
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot.params import CommandArg

from bot.config import settings
from bot.services.roles import is_super_admin
from bot.services.runtime import passive_settings
from bot.services.game_api_gate import GAME_API_PREFIX, game_message_disposition


@event_preprocessor
async def _(event: MessageEvent):
    text = event.get_plaintext().strip()
    store = passive_settings()
    if isinstance(event, GroupMessageEvent):
        disposition = game_message_disposition(
            text,
            is_group=True,
            master_enabled=settings.gsuid_enabled,
            hot_enabled=store.is_game_api_enabled(),
            in_scope=int(event.group_id) in store.groups("game_api"),
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


game_interface = on_command("游戏接口", priority=5, block=True)


def game_api_status_text() -> str:
    store = passive_settings()
    groups = store.groups("game_api")
    if set(groups) == set(settings.managed_group_ids):
        scope_text = f"全部管理群（{len(groups)} 个）"
    elif groups:
        scope_text = ",".join(str(group_id) for group_id in sorted(groups))
    else:
        scope_text = "未开启任何群"
    return (
        "游戏接口状态\n"
        f"Core 主开关（GSUID_ENABLED）：{'开启' if settings.gsuid_enabled else '关闭'}\n"
        f"运行热开关：{'开启' if store.is_game_api_enabled() else '关闭'}\n"
        f"识别前缀：{GAME_API_PREFIX}（可带或不带 #，大小写不敏感，仅群聊）\n"
        f"有效群：{scope_text}\n"
        "异环：启用；原神/鸣潮：未启用。\n"
        "热开关与范围修改均实时同步回 .env。\n"
        f"范围维护：#功能范围 游戏接口 添加|移除 QQ群号"
    )


@game_interface.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not is_super_admin(int(event.user_id)):
        await game_interface.finish("没有维护游戏接口的权限。")
    store = passive_settings()
    action = args.extract_plain_text().strip()
    if action in {"状态", "status"}:
        await game_interface.finish(game_api_status_text())
    if action in {"开启", "开", "on"}:
        store.set_game_api_enabled(True)
        await game_interface.finish("游戏接口热开关已开启。\n" + game_api_status_text())
    if action in {"关闭", "关", "off"}:
        store.set_game_api_enabled(False)
        await game_interface.finish("游戏接口热开关已关闭。\n" + game_api_status_text())
    await game_interface.finish("用法：#游戏接口 状态|开启|关闭")
