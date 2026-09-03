from __future__ import annotations

import re

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg

from bot.services.group_domains import FEATURES, GroupDomainService
from bot.services.hourly_announcements import HourlyAnnouncementService
from bot.services.qq_platform import QQPlatform, QQPlatformError
from bot.services.roles import is_super_admin
from bot.services.runtime import database, group_domains, passive_settings


domains: GroupDomainService = group_domains()
db = database()
hourly_service = HourlyAnnouncementService(db, lambda: domains.enabled_groups("hourly"))
passive = passive_settings()

group_settings = on_command(
    "群设置",
    aliases={
        "本群设置",
        "开关小游戏",
        "开关今日老婆",
        "开关准时报点",
        "开关被呼叫会话",
        "开关B站推送",
        "开关被动互动",
        "开关NTE",
        "开关鸣潮",
    },
    priority=3,
    block=True,
)
system_settings = on_command("系统设置", priority=3, block=True)


async def _is_group_admin(bot: Bot, event: GroupMessageEvent) -> bool:
    if is_super_admin(int(event.user_id)):
        return True
    try:
        member = await QQPlatform(bot).member_info(
            int(event.group_id), int(event.user_id)
        )
    except QQPlatformError:
        return False
    return str(member.get("role") or "member") in {"owner", "admin"}


def _feature_status(group_id: int, *, admin: bool) -> str:
    rows = domains.feature_rows(group_id)
    global_states = {
        feature_key: passive.is_chat_globally_enabled(feature_key)
        for feature_key in ("mention_chat", "proactive_chat")
    }
    game_api_enabled = passive.is_game_api_enabled()
    global_states.update({"nte": game_api_enabled, "ww": game_api_enabled})
    visible = [
        row
        for row in rows
        if admin
        or (
            row["effective_enabled"]
            and global_states.get(str(row["key"]), True)
        )
    ]
    lines = ["本群功能："]
    for row in visible:
        global_enabled = global_states.get(str(row["key"]), True)
        state = "开" if row["effective_enabled"] and global_enabled else "关"
        if row["configured_enabled"] and not row["effective_enabled"]:
            state = "已配置开启，依赖项关闭"
        elif row["configured_enabled"] and not global_enabled:
            state = "已配置开启，全局条件关闭"
        lines.append(f"{row['label']}：{state}")
        if admin and not row["configured_enabled"]:
            lines.append(f"  开启：#群设置 {row['label']} 开")
    if not visible:
        lines.append("暂无对普通成员开放的功能。")
    group = db.managed_group(group_id)
    alias = str(group["alias"] or group["group_name"] or group_id) if group else str(group_id)
    lines.insert(0, f"群代称：{alias}")
    if admin:
        lines.append("修改代称：#群设置 代称 <名称>")
        lines.append("群过滤：#群设置 过滤 添加|移除|列表 <QQ号>")
    return "\n".join(lines)


def _shortcut_feature(text: str) -> str | None:
    normalized = re.sub(r"^#\s*", "", str(text).strip(), count=1)
    if not normalized.startswith("开关"):
        return None
    return domains.normalize_feature(normalized[2:])


def _feature_action(raw: str) -> tuple[str, bool] | None:
    text = " ".join(str(raw).split())
    leading = re.fullmatch(r"(?:功能\s+)?(.+?)\s+(开|关|开启|关闭)", text)
    if leading:
        feature = domains.normalize_feature(leading.group(1))
        if feature:
            return feature, leading.group(2) in {"开", "开启"}
    trailing = re.fullmatch(r"(开|关|开启|关闭)\s+(.+)", text)
    if trailing:
        feature = domains.normalize_feature(trailing.group(2))
        if feature:
            return feature, trailing.group(1) in {"开", "开启"}
    return None


def _switch(value: str) -> bool | None:
    normalized = str(value).strip().casefold()
    if normalized in {"开", "开启", "on", "true"}:
        return True
    if normalized in {"关", "关闭", "off", "false"}:
        return False
    return None


def _passive_status(group_id: int) -> str:
    current = passive.for_group(group_id)
    return (
        f"群 {group_id} 被动互动参数\n"
        f"表情命中率：{current.reaction_probability * 100:g}%\n"
        f"表情冷却：{current.reaction_cooldown_seconds} 秒\n"
        f"复读命中率：{current.repeat_probability * 100:g}%\n"
        f"复读冷却：{current.repeat_cooldown_seconds // 60} 分钟\n"
        f"复读间隔：{current.repeat_message_interval} 条\n"
        f"三连复读：{'开' if current.triple_repeat_enabled else '关'}\n"
        f"三连命中率：{current.triple_repeat_probability * 100:g}%"
    )


def _percent(value: str, maximum: float) -> float | None:
    normalized = str(value).strip()
    try:
        parsed = float(normalized[:-1]) / 100 if normalized.endswith("%") else float(normalized)
    except ValueError:
        return None
    if not normalized.endswith("%") and parsed > 1:
        parsed /= 100
    return parsed if 0 <= parsed <= maximum else None


def _feature_update_text(feature_key: str, enabled: bool) -> str:
    label = FEATURES[feature_key].label
    if enabled:
        if feature_key in {"mention_chat", "proactive_chat"}:
            global_enabled = passive.is_chat_globally_enabled(feature_key)
        elif feature_key in {"nte", "ww"}:
            global_enabled = passive.is_game_api_enabled()
        else:
            global_enabled = True
        if not global_enabled:
            return f"{label}本群开关已开启；全局条件关闭，当前仍不生效。"
    return f"{label}已{'开启' if enabled else '关闭'}。"


@group_settings.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()) -> None:
    if not isinstance(event, GroupMessageEvent):
        await group_settings.finish("群设置只能在目标 QQ 群内使用。")
    group_id = int(event.group_id)
    domains.ensure_group(group_id)
    admin = await _is_group_admin(bot, event)
    raw = args.extract_plain_text().strip()
    shortcut = _shortcut_feature(event.get_plaintext())
    if shortcut is not None:
        if not admin:
            await group_settings.finish("只有本群群主、群管理员或超级管理员可以修改。")
        enabled = not domains.feature_enabled(group_id, shortcut)
        domains.set_feature(group_id, shortcut, enabled)
        await group_settings.finish(_feature_update_text(shortcut, enabled))

    if not raw or raw in {"状态", "列表", "功能"}:
        await group_settings.finish(_feature_status(group_id, admin=admin))
    if not admin:
        await group_settings.finish("只有本群群主、群管理员或超级管理员可以修改。")

    if raw.startswith("代称 "):
        alias = raw[3:].strip()
        try:
            domains.set_alias(group_id, alias)
        except ValueError:
            await group_settings.finish("代称需要 1-20 个可见字符。")
        db.audit(int(event.user_id), "group_alias_update", group_id, alias)
        await group_settings.finish(f"本群代称已改为：{alias}")

    if raw.startswith("过滤 "):
        tokens = raw.split()
        action = tokens[1] if len(tokens) > 1 else ""
        if action == "列表" and len(tokens) == 2:
            members = db.group_filter_members(group_id)
            await group_settings.finish(
                "本群过滤名单：" + ("、".join(map(str, members)) if members else "无")
            )
        if len(tokens) != 3 or not tokens[2].isdigit() or int(tokens[2]) <= 0:
            await group_settings.finish("用法：#群设置 过滤 添加|移除|列表 <QQ号>")
        target = int(tokens[2])
        if action == "添加":
            db.add_group_filter(group_id, target, int(event.user_id))
            db.audit(int(event.user_id), "group_filter_add", group_id, str(target))
            await group_settings.finish(f"已将 {target} 加入本群过滤名单。")
        if action == "移除":
            db.remove_group_filter(group_id, target)
            db.audit(int(event.user_id), "group_filter_remove", group_id, str(target))
            await group_settings.finish(f"已将 {target} 移出本群过滤名单。")
        await group_settings.finish("用法：#群设置 过滤 添加|移除|列表 <QQ号>")

    action = _feature_action(raw)
    if action is not None:
        feature_key, enabled = action
        domains.set_feature(group_id, feature_key, enabled)
        db.audit(
            int(event.user_id),
            "group_feature_update",
            group_id,
            f"feature={feature_key};enabled={str(enabled).lower()}",
        )
        await group_settings.finish(_feature_update_text(feature_key, enabled))

    await group_settings.finish(
        "用法：#群设置 / #群设置 <功能> 开|关 / #群设置 代称 <名称>"
    )


@system_settings.handle()
async def _(event: MessageEvent, args: Message = CommandArg()) -> None:
    if not is_super_admin(int(event.user_id)):
        await system_settings.finish("没有系统设置权限。")
    if isinstance(event, GroupMessageEvent):
        await system_settings.finish("系统设置只在私聊中使用。")
    tokens = args.extract_plain_text().strip().split()
    if not tokens:
        await system_settings.finish(
            "系统设置：\n"
            "#系统设置 集群 列表|创建|邀请|移除|解散\n"
            "#系统设置 游戏接口 状态|开|关\n"
            "#系统设置 小游戏 全局 状态|开|关\n"
            "#系统设置 被呼叫会话 状态|开|关\n"
            "#系统设置 糖糖主动聊天 状态|开|关\n"
            "#系统设置 小游戏 禁言 <群号> 状态|开|关\n"
            "#系统设置 被动互动 <群号> 状态|<参数> <值>\n"
            "#系统设置 准时报点 状态|开|关|时段 HH:MM HH:MM"
        )

    if tokens[0].casefold() in {"nte", "鸣潮", "ww", "游戏接口"}:
        action = tokens[1] if len(tokens) == 2 else "状态"
        if action in {"状态", "status"}:
            await system_settings.finish(
                f"NTE/鸣潮全局运行条件：{'开' if passive.is_game_api_enabled() else '关'}。"
            )
        enabled = _switch(action)
        if enabled is None:
            await system_settings.finish("用法：#系统设置 游戏接口 状态|开|关")
        passive.set_game_api_enabled(enabled)
        await system_settings.finish(
            f"NTE/鸣潮全局运行条件已{'开启' if enabled else '关闭'}；各群开关保持不变。"
        )

    chat_targets = {
        "被呼叫会话": ("mention_chat", "被呼叫会话"),
        "被呼叫回话": ("mention_chat", "被呼叫会话"),
        "被呼叫": ("mention_chat", "被呼叫会话"),
        "糖糖会话": ("mention_chat", "被呼叫会话"),
        "糖糖主动聊天": ("proactive_chat", "糖糖主动聊天"),
        "主动聊天": ("proactive_chat", "糖糖主动聊天"),
        "主动回复": ("proactive_chat", "糖糖主动聊天"),
    }
    if tokens[0] in chat_targets:
        feature_key, label = chat_targets[tokens[0]]
        action = tokens[1] if len(tokens) == 2 else "状态"
        if action in {"状态", "status"} and len(tokens) in {1, 2}:
            await system_settings.finish(
                f"{label}全局运行条件："
                f"{'开' if passive.is_chat_globally_enabled(feature_key) else '关'}。"
            )
        enabled = _switch(action) if len(tokens) == 2 else None
        if enabled is None:
            await system_settings.finish(
                f"用法：#系统设置 {label} 状态|开|关"
            )
        passive.set_chat_globally_enabled(feature_key, enabled)
        db.audit(
            int(event.user_id),
            "system_chat_global_update",
            detail=f"feature={feature_key};enabled={str(enabled).lower()}",
        )
        await system_settings.finish(
            f"{label}全局运行条件已{'开启' if enabled else '关闭'}；"
            "各群开关保持不变。"
        )

    if tokens[:2] == ["小游戏", "全局"]:
        action = tokens[2] if len(tokens) == 3 else "状态"
        if action in {"状态", "status"}:
            await system_settings.finish(
                f"小游戏全局运行条件：{'开' if passive.is_game_globally_enabled() else '关'}。"
            )
        enabled = _switch(action)
        if enabled is None:
            await system_settings.finish("用法：#系统设置 小游戏 全局 状态|开|关")
        passive.set_game_globally_enabled(enabled)
        await system_settings.finish(
            f"小游戏全局运行条件已{'开启' if enabled else '关闭'}；各群开关保持不变。"
        )

    if tokens[:2] == ["小游戏", "禁言"]:
        if len(tokens) not in {3, 4} or not tokens[2].isdigit():
            await system_settings.finish("用法：#系统设置 小游戏 禁言 <群号> 状态|开|关")
        group_id = int(tokens[2])
        if not db.is_managed_group(group_id):
            await system_settings.finish("该群当前不在机器人群列表中。")
        action = tokens[3] if len(tokens) == 4 else "状态"
        if action in {"状态", "status"}:
            await system_settings.finish(
                f"群 {group_id} 小游戏处罚禁言：{'开' if passive.is_game_mute_enabled(group_id) else '关'}。"
            )
        enabled = _switch(action)
        if enabled is None:
            await system_settings.finish("用法：#系统设置 小游戏 禁言 <群号> 状态|开|关")
        if enabled:
            passive.remove_feature_group("game_mute_disabled", group_id)
        else:
            passive.add_feature_group("game_mute_disabled", group_id)
        await system_settings.finish(
            f"群 {group_id} 小游戏处罚禁言已{'开启' if enabled else '关闭'}。"
        )

    if tokens[0] == "被动互动":
        if len(tokens) < 2 or not tokens[1].isdigit():
            await system_settings.finish(
                "用法：#系统设置 被动互动 <群号> 状态|表情命中率|表情冷却|复读命中率|复读冷却|复读间隔|三连复读|三连命中率 <值>"
            )
        group_id = int(tokens[1])
        if not db.is_managed_group(group_id):
            await system_settings.finish("该群当前不在机器人群列表中。")
        action = tokens[2] if len(tokens) >= 3 else "状态"
        if action in {"状态", "status"} and len(tokens) == 3:
            await system_settings.finish(_passive_status(group_id))
        if len(tokens) != 4:
            await system_settings.finish(
                "用法：#系统设置 被动互动 <群号> <参数> <值>"
            )
        value = tokens[3]
        if action == "表情命中率" and (parsed := _percent(value, 0.5)) is not None:
            passive.set_reaction_probability(group_id, parsed)
        elif action == "表情冷却" and value.isdigit() and 0 <= int(value) <= 3600:
            passive.set_reaction_cooldown_seconds(group_id, int(value))
        elif action == "复读命中率" and (parsed := _percent(value, 0.1)) is not None:
            passive.set_repeat_probability(group_id, parsed)
        elif action == "复读冷却" and value.isdigit() and 0 <= int(value) <= 1440:
            passive.set_repeat_cooldown_seconds(group_id, int(value) * 60)
        elif action == "复读间隔" and value.isdigit() and 0 <= int(value) <= 10000:
            passive.set_repeat_message_interval(group_id, int(value))
        elif action == "三连复读" and (enabled := _switch(value)) is not None:
            passive.set_triple_repeat_enabled(group_id, enabled)
        elif action == "三连命中率" and (parsed := _percent(value, 1.0)) is not None:
            passive.set_triple_repeat_probability(group_id, parsed)
        else:
            await system_settings.finish("参数名或数值超出允许范围。")
        db.audit(
            int(event.user_id),
            "system_passive_setting_update",
            group_id,
            f"parameter={action};value={value}",
        )
        await system_settings.finish(_passive_status(group_id))

    if tokens and tokens[0] in {"准时报点", "整点报时"}:
        action = tokens[1] if len(tokens) > 1 else "状态"
        if action == "状态" and len(tokens) in {1, 2}:
            await system_settings.finish(hourly_service.schedule_text())
        if action in {"开启", "开"} and len(tokens) == 2:
            hourly_service.set_enabled(True)
            await system_settings.finish("准时报点全局运行条件已开启；各群意图保持不变。")
        if action in {"关闭", "关"} and len(tokens) == 2:
            hourly_service.set_enabled(False)
            await system_settings.finish("准时报点全局运行条件已关闭；各群意图保持不变。")
        if action == "时段" and len(tokens) == 4:
            clocks = []
            for value in tokens[2:]:
                match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
                if match is None:
                    await system_settings.finish("时间格式必须是 HH:MM。")
                clocks.append(int(match.group(1)) * 60 + int(match.group(2)))
            hourly_service.set_schedule(clocks[0], clocks[1])
            await system_settings.finish("准时报点时段已更新。")
        await system_settings.finish(
            "用法：#系统设置 准时报点 状态|开启|关闭|时段 HH:MM HH:MM"
        )
    if tokens[:2] == ["集群", "列表"]:
        with db.connect() as connection:
            rows = list(
                connection.execute(
                    """SELECT domain_id,name,alias FROM group_domains
                       WHERE mode='cluster' AND enabled=1 ORDER BY domain_id"""
                )
            )
        lines = [
            f"{row['domain_id']}：{row['alias'] or row['name']}（{len(domains.domain_groups(int(row['domain_id'])))} 群）"
            for row in rows
        ]
        await system_settings.finish("集群：\n" + ("\n".join(lines) if lines else "无"))

    if tokens[:2] == ["集群", "创建"] and len(tokens) >= 3:
        cluster = domains.create_cluster(" ".join(tokens[2:]))
        await system_settings.finish(f"集群已创建，内部 ID：{cluster.domain_id}")
    if tokens[:2] == ["集群", "邀请"] and len(tokens) == 4:
        if not all(value.isdigit() for value in tokens[2:]):
            await system_settings.finish("用法：#系统设置 集群 邀请 <集群ID> <群号>")
        if not db.is_managed_group(int(tokens[3])):
            await system_settings.finish("该群尚未加入机器人，不能邀请到集群。")
        domains.add_group_to_cluster(int(tokens[3]), int(tokens[2]))
        await system_settings.finish("已加入集群，集群链接已轮换。")
    if tokens[:2] == ["集群", "移除"] and len(tokens) == 3 and tokens[2].isdigit():
        domains.remove_group_from_cluster(int(tokens[2]))
        await system_settings.finish("已移出集群并恢复为独群，原集群链接已轮换。")
    if tokens[:2] == ["集群", "解散"] and len(tokens) == 3 and tokens[2].isdigit():
        domains.dissolve_cluster(int(tokens[2]))
        await system_settings.finish("集群已解散，旧链接已失效。")
    await system_settings.finish(
        "用法：#系统设置 集群 列表|创建 <名称>|邀请 <集群ID> <群号>|移除 <群号>|解散 <集群ID>"
    )


__all__ = ["group_settings", "system_settings"]
