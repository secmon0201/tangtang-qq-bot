"""Super-admin audit and correction commands for the skill platform."""
from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, Message
from nonebot.params import CommandArg

from bot.services.roles import is_super_admin
from bot.services.skill_audit import CATEGORIES, ledger
from bot.services.skill_capability import capabilities
from bot.services.skill_metrics import metrics
from bot.services.skills import SkillRegistryError, registry_loader


skill_admin = on_command(
    "技能",
    aliases={"技能列表", "技能开关", "技能纠错"},
    priority=3,
    block=True,
)


def _require_super_admin(event: MessageEvent) -> str | None:
    if not is_super_admin(int(event.user_id)):
        return "技能审计仅限超级管理员。"
    return None


@skill_admin.handle()
async def handle_skill_admin(event: MessageEvent, args: Message = CommandArg()) -> None:
    denied = _require_super_admin(event)
    if denied:
        await skill_admin.finish(denied)
    tokens = args.extract_plain_text().split()
    if not tokens or tokens == ["状态"]:
        try:
            registry = registry_loader.load()
        except SkillRegistryError as exc:
            await skill_admin.finish(f"技能注册表不可用：{type(exc).__name__}")
        summary = ledger.summary()
        upstream = capabilities.snapshot()
        upstream_text = "、".join(
            f"{item['name']}:{'可用' if item['usable'] else '不可用'}"
            for item in upstream
        )
        await skill_admin.finish(
            "技能平台状态：\n"
            f"注册技能：{len(registry.skills)}\n"
            f"本地动作：{len(registry.action_map)}\n"
            f"上游能力：{upstream_text or '无'}\n"
            f"纠错账本：{summary['by_status'] or '空'}\n"
            f"未解决最久：{int(summary['oldest_open_seconds'])} 秒"
        )
    if tokens[:2] == ["纠错", "列表"]:
        status = tokens[2] if len(tokens) > 2 else "open"
        entries = ledger.list_entries(status=status, limit=10)
        if not entries:
            await skill_admin.finish(f"没有状态为 {status} 的纠错记录。")
        lines = [
            f"{index + 1}. [{entry.category}/{entry.severity}] "
            f"{entry.skill_id or '未命名'}：{entry.detail[:60] or entry.source}"
            for index, entry in enumerate(entries)
        ]
        await skill_admin.finish("技能纠错记录：\n" + "\n".join(lines))
    if tokens[:2] == ["纠错", "解决"] and len(tokens) >= 3:
        entry = ledger.get(tokens[2])
        if entry is None:
            await skill_admin.finish("没有找到这条纠错记录。")
        changed = ledger.resolve(
            tokens[2], " ".join(tokens[3:]) or "由超级管理员标记解决"
        )
        await skill_admin.finish("已标记解决。" if changed else "该记录已经解决。")
    if tokens == ["纠错", "类别"]:
        await skill_admin.finish("可用类别：" + "、".join(sorted(CATEGORIES)))
    if tokens == ["列表"]:
        registry = registry_loader.load()
        lines = [
            f"{skill.skill_id}（v{skill.version}，{skill.kind}）："
            f"{'启用' if ledger.control(skill.skill_id)['enabled'] else '停用'}"
            for skill in registry.skills
        ]
        await skill_admin.finish("技能清单：\n" + "\n".join(lines))
    if tokens[:2] == ["开关", "开"] and len(tokens) >= 3:
        groups = tuple(int(item) for item in tokens[3:] if item.isdigit())
        ledger.set_control(tokens[2], enabled=True, groups=groups, note="admin enable")
        scope = "、".join(str(item) for item in groups) if groups else "全部群"
        await skill_admin.finish(f"已启用技能 {tokens[2]}（{scope}）。")
    if tokens[:2] == ["开关", "关"] and len(tokens) >= 3:
        ledger.set_control(tokens[2], enabled=False, note="admin disable")
        await skill_admin.finish(f"已停用技能 {tokens[2]}。")
    if tokens[:2] == ["开关", "状态"] and len(tokens) >= 3:
        control = ledger.control(tokens[2])
        groups = "、".join(str(item) for item in control["groups"]) or "全部群"
        await skill_admin.finish(
            f"技能 {tokens[2]}：{'启用' if control['enabled'] else '停用'}；范围：{groups}"
        )
    if tokens[:2] == ["数据", "清理"] and len(tokens) >= 3 and tokens[2].isdigit():
        user_id = int(tokens[2])
        removed = ledger.purge_user(user_id)
        removed.update(metrics.purge_user(user_id))
        await skill_admin.finish(
            f"已清理该用户的技能审计与用量记录：审计 {removed['skill_audit_entries']} 条，"
            f"用量 {removed['skill_usage']} 条。"
        )
    if tokens == ["数据", "导出"]:
        from bot import config as bot_config

        target = bot_config.ROOT / "data" / "skills" / "export-audit.json"
        count = ledger.export(target)
        await skill_admin.finish(f"已导出最近 {count} 条技能审计记录到本机数据目录。")
    await skill_admin.finish(
        "用法：#技能 状态 / #技能 列表 / #技能 开关 开|关 <技能ID> [群号...] / "
        "#技能 开关 状态 <技能ID> / #技能 纠错 列表 [状态] / "
        "#技能 纠错 解决 <编号> [说明] / #技能 纠错 类别 / "
        "#技能 数据 清理 <QQ号> / #技能 数据 导出"
    )
