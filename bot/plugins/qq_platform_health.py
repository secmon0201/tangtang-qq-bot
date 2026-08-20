"""Operator-triggered, read-only OneBot transport capability check."""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from bot.services.qq_platform import QQPlatformError, qq_platform
from bot.services.roles import is_super_admin


platform_health = on_command("QQ平台自检", priority=5, block=True)
platform_health._tangtang_skip_quote = True


@platform_health.handle()
async def _(bot: Bot, event: GroupMessageEvent) -> None:
    if not is_super_admin(int(event.user_id)):
        await platform_health.finish("仅超级管理员可执行 QQ 平台自检。")

    platform = qq_platform(bot)
    try:
        login = await platform.login_info()
        group = await platform.group_info(int(event.group_id))
        members = await platform.member_list(int(event.group_id))
    except QQPlatformError as exc:
        await platform_health.finish(f"QQ 平台自检失败：{exc}")

    await platform_health.finish(
        "QQ 平台自检通过\n"
        f"机器人：{login.get('nickname') or login.get('user_id')}\n"
        f"群：{group.get('group_name') or event.group_id}\n"
        f"成员列表：{len(members)} 人"
    )
