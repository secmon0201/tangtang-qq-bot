from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.params import CommandArg

from bot.application.command_helpers import (
    bounded_integer,
    current_group,
    is_operator,
    percent_value,
    text_arg,
    user_id,
)
from bot.services.env_sync import update_env_value
from bot.services.runtime import database, passive_settings
from bot.services.tangtang_chat import TangtangConfig
from bot.services.tangtang_runtime import config_loader as loader


db = database()


def _status_text(
    config: TangtangConfig,
    title: str = "糖糖主动回复当前配置",
    *,
    system_enabled: bool = True,
) -> str:
    groups = "、".join(str(group_id) for group_id in sorted(config.group_ids)) or "无"
    group_values = "\n".join(
        f"{group_id}：{probability * 100:g}% / {cooldown // 60} 分钟 / {interval} 条"
        for group_id, probability, cooldown, interval in (
            (group_id, *config.proactive_values_for(group_id))
            for group_id in config.group_order
        )
    ) or "无"
    return (
        f"{title}。\n"
        f"糖糖总开关：{'开启' if config.enabled else '关闭'}\n"
        f"主动回复参数开关：{'开启' if config.proactive_enabled else '关闭'}\n"
        f"系统总控：{'开启' if system_enabled else '关闭'}\n"
        f"命中率：{config.proactive_probability * 100:g}%\n"
        f"冷却：{config.proactive_cooldown_seconds // 60} 分钟\n"
        f"消息间隔：{config.proactive_message_interval} 条\n"
        f"生效群：{groups}\n"
        f"逐群参数（命中率 / 冷却 / 间隔）：\n{group_values}"
    )


def _current_status_text(
    config: TangtangConfig, title: str = "糖糖主动回复当前配置"
) -> str:
    return _status_text(
        config,
        title,
        system_enabled=passive_settings().is_chat_globally_enabled(
            "proactive_chat"
        ),
    )


def _write_env(key: str, value: str, config: TangtangConfig | None = None) -> str | None:
    """Write one proactive key back to .env; return an error message on failure."""

    try:
        if key != "TANGTANG_PROACTIVE_ENABLED" and config is not None and config.group_order:
            value = ",".join([value] * len(config.group_order))
        update_env_value(key, value)
    except (OSError, ValueError) as exc:
        return f".env 写入失败：{exc}"
    return None


proactive_reply = on_command(
    "糖糖主动回复",
    aliases={"糖糖主动", "主动回复"},
    priority=5,
    block=True,
)


@proactive_reply.handle()
async def _(event: MessageEvent, args=CommandArg()):
    if not is_operator(event):
        await proactive_reply.finish("没有维护糖糖主动回复参数的权限。")
    tokens = text_arg(args).split()
    config = loader.load()
    if not tokens or tokens[0] in {"状态", "status"}:
        await proactive_reply.finish(_current_status_text(config))

    action = tokens[0].lower()
    if action in {"开启", "打开", "on"} and len(tokens) == 1:
        error = _write_env("TANGTANG_PROACTIVE_ENABLED", "true")
        if error:
            await proactive_reply.finish(error)
        db.audit(user_id(event), "tangtang_proactive_enable", current_group(event), "true")
        await proactive_reply.finish(
            _current_status_text(loader.load(), "糖糖主动回复已开启")
        )

    if action in {"关闭", "停用", "off"} and len(tokens) == 1:
        error = _write_env("TANGTANG_PROACTIVE_ENABLED", "false")
        if error:
            await proactive_reply.finish(error)
        db.audit(user_id(event), "tangtang_proactive_enable", current_group(event), "false")
        await proactive_reply.finish(
            _current_status_text(loader.load(), "糖糖主动回复已关闭")
        )

    if action in {"概率", "命中率", "probability"}:
        value = percent_value(tokens[1], 1.0) if len(tokens) == 2 else None
        if value is None:
            await proactive_reply.finish("用法：#糖糖主动回复 概率 0-100%")
        error = _write_env("TANGTANG_PROACTIVE_PROBABILITY", str(value), config)
        if error:
            await proactive_reply.finish(error)
        db.audit(
            user_id(event),
            "tangtang_proactive_probability",
            current_group(event),
            f"value={value}",
        )
        await proactive_reply.finish(
            _current_status_text(loader.load(), "糖糖主动回复命中率已更新")
        )

    if action in {"冷却", "cooldown"}:
        minutes = bounded_integer(tokens[1], 0, 1440) if len(tokens) == 2 else None
        if minutes is None:
            await proactive_reply.finish("用法：#糖糖主动回复 冷却 0-1440（分钟）")
        error = _write_env("TANGTANG_PROACTIVE_COOLDOWN_SECONDS", str(minutes * 60), config)
        if error:
            await proactive_reply.finish(error)
        db.audit(
            user_id(event),
            "tangtang_proactive_cooldown",
            current_group(event),
            f"value={minutes * 60}",
        )
        await proactive_reply.finish(
            _current_status_text(loader.load(), "糖糖主动回复冷却已更新")
        )

    if action in {"间隔", "interval"}:
        value = bounded_integer(tokens[1], 0, 10000) if len(tokens) == 2 else None
        if value is None:
            await proactive_reply.finish("用法：#糖糖主动回复 间隔 0-10000（条消息）")
        error = _write_env("TANGTANG_PROACTIVE_MESSAGE_INTERVAL", str(value), config)
        if error:
            await proactive_reply.finish(error)
        db.audit(
            user_id(event),
            "tangtang_proactive_interval",
            current_group(event),
            f"value={value}",
        )
        await proactive_reply.finish(
            _current_status_text(loader.load(), "糖糖主动回复消息间隔已更新")
        )

    await proactive_reply.finish(
        "用法：#糖糖主动回复 状态|开启|关闭|概率 0-100%|冷却 0-1440分钟|间隔 0-10000条"
    )
