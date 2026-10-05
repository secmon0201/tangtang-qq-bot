"""Read legacy source/registry without importing it; generate only new docs."""
from __future__ import annotations

import ast
import json
from pathlib import Path


NEW_ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = NEW_ROOT.parent
TARGETS = {
    "commands": "tools：帮助、统计排行、查重、白名单、状态；web 管理页",
    "stats": "tools.ranking + 独立事件统计",
    "a_coast_archive": "tools：archive_records / archive_search / archive_profile",
    "mini_games": "tools：转盘、炸弹、成语、骰子、猜数、排行榜及清理；background",
    "today_wife": "tools：wife_*；background 的缘分集体互动",
    "asoul": "tools：asoul_* / bili_*；background B站轮询与日程",
    "zhijiang": "tools：zhijiang_*；business.zhijiang_live_guard",
    "random_reactions": "background：随机表情、复读、三重复读（不调用模型）",
    "denia_gallery": "tools.denia_gallery + resources/personas/denia/gallery",
    "knowledge_review": "tools：knowledge_search / knowledge_review",
    "global_announcement": "tools.global_announcement + 新 API 公告预览/发送",
    "operator_web": "新 API 查重/白名单页面能力，不引用旧 server_app",
    "group_settings": "tools：group_settings / system_settings + 新库 group_domains",
    "scope": "runtime 输入范围与实际操作权限；不迁移 NoneBot preprocessor",
    "outbound_pacing": "onebot API 执行与回执；新运行时发送队列",
    "game_api": "external Core 协议 adapter，#nte/#ww 唯一执行者透传",
    "nte_game_ui": "明确不迁移本地接管；上游原样透传",
    "wuwa_game_ui": "明确不迁移本地接管；上游原样透传",
    "tangtang_chat": "chat/context/media：独立模型循环、分层、usage 与交付",
    "persona_management": "人格资源、记忆、后台整理与 speech；tools.persona_*",
    "tangtang_model_switch": "模型 profiles + /api/models + tools.model_settings",
    "tangtang_proactive": "proactive_policy / continuation_policy + 后台调度",
    "hourly_announcements": "background：整点报时、时段与来源；本地文案",
    "qq_platform_health": "tools.qq_platform_health + /api/status",
    "qq_transport_maintenance": "独立运行状态与维护记录；不接管旧 watchdog",
    "runtime_maintenance": "新目录自有缓存维护，保留备份，不清旧目录",
    "skill_admin": "工具目录、实际执行记录与模型用量；不重造审计平台",
    "codex_completion": "新 API 内部通知入口；测试群真实发送另验收",
    "official_qq": "当前明确不迁移：新框架只通过 SnowLuma OneBot 接入",
}


def literal(node: ast.AST, names: dict[str, object] | None = None) -> object | None:
    names = names or {}
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        items = []
        for part in node.elts:
            value = literal(part.value if isinstance(part, ast.Starred) else part, names)
            if isinstance(part, ast.Starred):
                if not isinstance(value, (set, frozenset, list, tuple)):
                    return None
                items.extend(value)
            else:
                items.append(value)
        return set(items) if isinstance(node, ast.Set) else tuple(items) if isinstance(node, ast.Tuple) else items
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"frozenset", "set", "tuple", "list"} and len(node.args) == 1:
        value = literal(node.args[0], names)
        if isinstance(value, (set, frozenset, list, tuple, dict)):
            return {"frozenset": frozenset, "set": set, "tuple": tuple, "list": list}[node.func.id](value)
        return None
    if isinstance(node, ast.BinOp):
        left, right = literal(node.left, names), literal(node.right, names)
        if isinstance(node.op, ast.BitOr) and isinstance(left, (set, frozenset)) and isinstance(right, (set, frozenset)):
            return left | right
        if isinstance(node.op, ast.Add) and isinstance(left, (str, tuple, list)) and isinstance(right, type(left)):
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            value = literal(part.value if isinstance(part, ast.FormattedValue) else part, names)
            if value is None:
                return None
            parts.append(str(value))
        return "".join(parts)
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def constants(tree: ast.Module, cache: dict[str, dict[str, object]] | None = None) -> dict[str, object]:
    """Resolve project-owned static constants only, without importing Python."""
    cache = cache if cache is not None else {}
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bot."):
            wanted = [name for name in node.names if name.name.isupper()]
            if not wanted:
                continue
            module = node.module
            path = LEGACY_ROOT.joinpath(*module.split(".")).with_suffix(".py")
            if module not in cache and path.is_file():
                cache[module] = {}
                cache[module] = constants(ast.parse(path.read_text(encoding="utf-8-sig")), cache)
            for name in wanted:
                if name.name in cache.get(module, {}):
                    values[name.asname or name.name] = cache[module][name.name]
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = literal(node.value, values)
            if value is not None:
                for target in targets:
                    if isinstance(target, ast.Name):
                        values[target.id] = value
    return values


def command_strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {key for key in value if isinstance(key, str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {item for item in value if isinstance(item, str)}
    return set()


def inventory() -> list[dict[str, object]]:
    registry_path = LEGACY_ROOT / "bot/application/plugin_registry.py"
    registry_tree = ast.parse(registry_path.read_text(encoding="utf-8-sig"))
    specs = []
    for node in ast.walk(registry_tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "PluginSpec":
            args = [literal(arg) for arg in node.args]
            specs.append({"key": args[0], "module": args[1], "label": args[2], "category": args[3], "line": node.lineno})
    skills = json.loads((LEGACY_ROOT / "config/skill-registry.json").read_text(encoding="utf-8-sig"))["skills"]
    for spec in specs:
        key = str(spec["key"])
        relative = "bot/plugins/" + key + ".py"
        source = (LEGACY_ROOT / relative).read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        names = constants(tree)
        unresolved = []
        commands: dict[str, dict[str, object]] = {}
        for skill in skills:
            if skill.get("plugin") == relative:
                for entry in skill.get("commands", []):
                    command = entry["command"]
                    commands[command] = {"name": command, "aliases": set(entry.get("aliases", [])), "line": None, "basis": "技能清单"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "on_command" and node.args:
                command = literal(node.args[0], names)
                if isinstance(command, str):
                    aliases = set()
                    for keyword in node.keywords:
                        if keyword.arg == "aliases":
                            value = literal(keyword.value, names)
                            aliases = command_strings(value)
                            if value is None:
                                unresolved.append({"expression": ast.unparse(keyword.value), "line": node.lineno})
                    entry = commands.setdefault(command, {"name": command, "aliases": set()})
                    entry["aliases"].update(aliases)
                    entry.update(line=node.lineno, basis="源码 matcher")
                else:
                    unresolved.append({"expression": ast.unparse(node.args[0]), "line": node.lineno})
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and ("COMMAND" in target.id or target.id == "RANKING_COMMANDS") for target in node.targets):
                value = literal(node.value, names)
                for command in command_strings(value):
                    if command.startswith("#"):
                        name = command[1:]
                        commands.setdefault(name, {"name": name, "aliases": set(), "line": node.lineno, "basis": "源码命令集合"})
        entrypoints = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"on_message", "on_notice", "on_request"}:
                rule = next((ast.unparse(keyword.value) for keyword in node.keywords if keyword.arg == "rule"), "无显式 rule")
                entrypoints.append({"kind": node.func.id, "rule": rule, "line": node.lineno})
        spec.update(source=relative, commands=sorted(commands.values(), key=lambda item: str(item["name"])), entrypoints=entrypoints, unresolved=unresolved)
    return specs


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(specs: list[dict[str, object]]) -> str:
    lines = ["# 旧功能与新框架迁移台账", "", "本页由静态源码及能力清单生成，不导入旧模块、不读取实例数据库。它记录来源与目标，不能单独证明新功能已验收或旧进程已加载当前代码。", "", "运行验收分为：代码迁移、隔离环境行为测试、真实 QQ / 模型 / 缓存验收。最后一项尚未发生时保持待验收。NTE/鸣潮只透传；官方 QQ 备用传输不迁移；退休功能不重新启用。", "", "## 全部插件来源", "", "| 功能 | 旧来源 | 新目标与边界 |", "| --- | --- | --- |"]
    for spec in specs:
        source = str(spec["source"])
        key = str(spec["key"])
        lines.append(f"| {cell(spec['label'])} | `{source}`；registry:{spec['line']} | {cell(TARGETS[key])} |")
    lines.extend(["", "## 命令与别名", "", "下表合并 `on_command`、源码命令集合与 `config/skill-registry.json`，静态展开常量引用、集合合并和别名。源码行号为空的条目来自清单，需要由消息路由或服务参数解析实现；它们不一定对应独立 matcher。动态声明另列，不假装已解析。参数语法、子命令与示例见[旧全部指令清单](../../docs/全部%23指令清单.md)及旧功能函数行为。", ""])
    for spec in specs:
        lines.extend([f"### {spec['label']}", "", f"旧来源：`{spec['source']}`；新目标：{TARGETS[str(spec['key'])]}。", ""])
        commands = spec["commands"]
        if commands:
            lines.extend(["| 主命令 | 别名 | 来源行 / 依据 |", "| --- | --- | --- |"])
            for command in commands:
                aliases = "、".join(f"`#{name}`" for name in sorted(command["aliases"])) or "—"
                origin = f"{command.get('line') or '—'} / {command.get('basis', '清单')}"
                lines.append(f"| `#{cell(command['name'])}` | {cell(aliases)} | {cell(origin)} |")
            lines.append("")
        if spec["entrypoints"]:
            lines.extend(["| 事件入口 | rule | 来源行 |", "| --- | --- | --- |"])
            for event in spec["entrypoints"]:
                lines.append(f"| `{event['kind']}` | `{cell(event['rule'])}` | {event['line']} |")
            lines.append("")
        if spec["unresolved"]:
            lines.extend(["| 尚需人工核对的动态命令声明 | 来源行 |", "| --- | --- |"])
            for item in spec["unresolved"]:
                lines.append(f"| `{cell(item['expression'])}` | {item['line']} |")
            lines.append("")
        if not commands and not spec["entrypoints"]:
            lines.extend(["无独立普通消息命令；检查其 Web API、生命周期钩子、后台任务或预处理器。", ""])
    lines.extend(["## 尚需真实验收", "", "- 同账号 SnowLuma 双 endpoint 入站，旧 8080 不受影响；新 8090 的事件与 API 回执相关性。", "- 非旧受管测试群的专用呼叫、工具文本/图片/转发及权限行为；避免双私聊自动回复。", "- 多模型实发上下文、提供商真实缓存字段、收费单价与请求延迟；离线前缀相同比较不代替缓存验收。", "- 原始图片、引用、历史轮次、黑名单、记忆、群摘要、压缩与失败交付的上下文行为。", "- Core `#nte/#ww` 唯一转发者及 route identity 切换；语音独立运行或共享固定声线竞争测试。", "- B站扫码由用户完成；轮询首次只建游标，不回放历史，不向旧群重复播报。", "- 最终停旧接单后的跨库稳定快照、增量去重和历史 pending 不补发。", ""])
    return "\n".join(lines)


def main() -> None:
    target = NEW_ROOT / "docs/旧功能迁移台账.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(inventory()), encoding="utf-8")
    print("已生成新目录迁移台账；旧源码只读，未读取实例配置或数据库。")


if __name__ == "__main__":
    main()
