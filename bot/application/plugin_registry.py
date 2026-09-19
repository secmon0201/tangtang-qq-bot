"""Declarative registry for independently loaded NoneBot feature plugins."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PluginSpec:
    key: str
    module: str
    label_zh: str
    category_zh: str
    transports: tuple[str, ...]
    setting: str | None = None
    after: tuple[str, ...] = ()


PLUGIN_SPECS = (
    PluginSpec("outbound_pacing", "bot.plugins.outbound_pacing", "消息发送节流", "基础设施", ("onebot",)),
    PluginSpec("scope", "bot.plugins.scope", "群组范围与权限门", "基础设施", ("onebot",)),
    PluginSpec(
        "group_settings",
        "bot.plugins.group_settings",
        "群设置与群域管理",
        "基础设施",
        ("onebot",),
        after=("scope",),
    ),
    PluginSpec(
        "stats",
        "bot.plugins.stats",
        "实时发言统计",
        "统计与档案",
        ("onebot",),
        setting="stats_realtime_enabled",
    ),
    PluginSpec("game_api", "bot.plugins.game_api", "游戏接口门", "游戏接口", ("onebot",)),
    PluginSpec(
        "nte_game_ui",
        "bot.plugins.nte_game_ui",
        "异环排行与帮助接管",
        "异环",
        ("onebot",),
        after=("game_api",),
    ),
    PluginSpec(
        "wuwa_game_ui",
        "bot.plugins.wuwa_game_ui",
        "鸣潮排行与帮助接管",
        "鸣潮",
        ("onebot",),
        after=("game_api",),
    ),
    PluginSpec(
        "qq_transport_maintenance",
        "bot.plugins.qq_transport_maintenance",
        "QQ 传输维护记录",
        "运行维护",
        ("onebot",),
    ),
    PluginSpec("qq_platform_health", "bot.plugins.qq_platform_health", "QQ 平台健康检查", "运行维护", ("onebot",)),
    PluginSpec("random_reactions", "bot.plugins.random_reactions", "随机互动与复读", "群聊互动", ("onebot",)),
    PluginSpec("denia_gallery", "bot.plugins.denia_gallery", "达妮娅美图", "糖糖", ("onebot",)),
    PluginSpec("tangtang_chat", "bot.plugins.tangtang_chat", "糖糖聊天", "糖糖", ("onebot",)),
    PluginSpec("persona_management", "bot.plugins.persona_management", "人格与语音管理", "糖糖", ("onebot",)),
    PluginSpec(
        "tangtang_model_switch",
        "bot.plugins.tangtang_model_switch",
        "糖糖模型切换",
        "糖糖",
        ("onebot",),
        after=("tangtang_chat",),
    ),
    PluginSpec(
        "tangtang_proactive",
        "bot.plugins.tangtang_proactive",
        "糖糖主动回复配置",
        "糖糖",
        ("onebot",),
        after=("tangtang_chat",),
    ),
    PluginSpec("commands", "bot.plugins.commands", "机器人基础指令", "基础指令", ("onebot",)),
    PluginSpec(
        "a_coast_archive",
        "bot.plugins.a_coast_archive",
        "群发言档案与画像",
        "统计与档案",
        ("onebot",),
        setting="stats_realtime_enabled",
        after=("commands",),
    ),
    PluginSpec("knowledge_review", "bot.plugins.knowledge_review", "知识库审核", "知识与资料", ("onebot",)),
    PluginSpec("mini_games", "bot.plugins.mini_games", "群聊小游戏", "群聊互动", ("onebot",)),
    PluginSpec("today_wife", "bot.plugins.today_wife", "今日老婆与缘分", "群聊互动", ("onebot",)),
    PluginSpec("runtime_maintenance", "bot.plugins.runtime_maintenance", "运行文件维护", "运行维护", ("onebot",)),
    PluginSpec("codex_completion", "bot.plugins.codex_completion", "Codex 完成通知", "运行维护", ("onebot",)),
    PluginSpec("zhijiang", "bot.plugins.zhijiang", "枝江直播与本地功能", "A-SOUL", ("onebot",)),
    PluginSpec("asoul", "bot.plugins.asoul", "A-SOUL 与 B 站接口", "A-SOUL", ("onebot",)),
    PluginSpec("global_announcement", "bot.plugins.global_announcement", "公告面板", "管理", ("onebot",)),
    PluginSpec(
        "operator_web",
        "bot.plugins.operator_web",
        "查重网页",
        "运行维护",
        ("onebot",),
        after=("commands",),
    ),
    PluginSpec("skill_admin", "bot.plugins.skill_admin", "技能审计", "运行维护", ("onebot",)),
    PluginSpec("hourly_announcements", "bot.plugins.hourly_announcements", "整点报时", "群聊互动", ("onebot",)),
    PluginSpec("official_qq", "bot.plugins.official_qq", "官方 QQ 模式", "备用传输", ("qq_openapi",)),
)


def _validate_registry() -> None:
    keys = [spec.key for spec in PLUGIN_SPECS]
    modules = [spec.module for spec in PLUGIN_SPECS]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate plugin registry key")
    if len(modules) != len(set(modules)):
        raise RuntimeError("duplicate plugin registry module")
    positions = {spec.key: index for index, spec in enumerate(PLUGIN_SPECS)}
    for spec in PLUGIN_SPECS:
        for dependency in spec.after:
            if dependency not in positions:
                raise RuntimeError(f"unknown plugin ordering dependency: {dependency}")
            if positions[dependency] >= positions[spec.key]:
                raise RuntimeError(f"plugin ordering dependency is not earlier: {spec.key} -> {dependency}")


def plugin_specs_for(
    transport: str,
    *,
    stats_realtime_enabled: bool,
) -> tuple[PluginSpec, ...]:
    flags = {"stats_realtime_enabled": stats_realtime_enabled}
    return tuple(
        spec
        for spec in PLUGIN_SPECS
        if transport in spec.transports
        and (spec.setting is None or flags.get(spec.setting, False))
    )


_validate_registry()


__all__ = ["PLUGIN_SPECS", "PluginSpec", "plugin_specs_for"]
