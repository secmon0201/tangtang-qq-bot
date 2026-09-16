from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg

from bot.application.command_helpers import current_group, is_operator, text_arg, user_id
from bot.services.runtime import database
from bot.services.tangtang_models import model_profile_status
from bot.services.tangtang_runtime import config_loader as loader


db = database()

model_switch = on_command("糖糖模型", priority=5, block=True)


@model_switch.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not is_operator(event):
        await model_switch.finish("没有切换糖糖模型的权限。")

    requested = text_arg(args)
    try:
        catalog = loader.model_profile_catalog()
    except (OSError, TypeError, ValueError) as exc:
        await model_switch.finish(f"糖糖模型档案不可用：{exc}")

    if not requested or requested.lower() in {"状态", "status"}:
        await model_switch.finish(model_profile_status(catalog))

    try:
        selected = catalog.profile(requested)
        config = loader.activate_model_profile(selected.name)
        updated = loader.model_profile_catalog()
    except (OSError, TypeError, ValueError) as exc:
        await model_switch.finish(
            f"无法切换到“{requested}”：{exc}\n"
            + model_profile_status(catalog)
        )

    if not config.enabled:
        await model_switch.finish("模型档案已保存，但糖糖配置校验失败，当前聊天已停用。")
    db.audit(
        user_id(event),
        "tangtang_model_profile_update",
        current_group(event),
        f"profile={selected.name};model={selected.model}",
    )
    await model_switch.finish(
        model_profile_status(updated, f"糖糖模型已切换为 {selected.name}")
    )
