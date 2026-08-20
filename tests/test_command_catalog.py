"""Prevent the user-facing # command catalog from drifting from active OneBot plugins."""

from __future__ import annotations

import ast
from pathlib import Path

from bot.application.plugin_registry import plugin_specs_for


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "全部#指令清单.md"


def _active_onebot_plugin_sources() -> tuple[Path, ...]:
    """Read every enabled OneBot feature from the declarative registry."""
    specs = plugin_specs_for("onebot", stats_realtime_enabled=True)
    return tuple(ROOT / (spec.module.replace(".", "/") + ".py") for spec in specs)


def _active_on_command_names() -> set[str]:
    names: set[str] = set()
    for path in _active_onebot_plugin_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "on_command" or not node.args:
                continue
            name = node.args[0]
            if isinstance(name, ast.Constant) and isinstance(name.value, str):
                names.add(name.value)
            for keyword in node.keywords:
                if keyword.arg != "aliases" or not isinstance(keyword.value, ast.Set):
                    continue
                for alias in keyword.value.elts:
                    if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                        names.add(alias.value)
    return names


def test_catalog_mentions_every_active_on_command_and_alias():
    catalog = CATALOG.read_text(encoding="utf-8")

    assert "本清单以当前运行的 **OneBot / NapCat** 模式为准" in catalog
    missing = sorted(name for name in _active_on_command_names() if f"#{name}" not in catalog)
    assert not missing, f"Update {CATALOG.name} for active commands: {', '.join(missing)}"


def test_catalog_covers_message_dispatched_mini_game_commands():
    catalog = CATALOG.read_text(encoding="utf-8")
    mini_game_commands = {
        "游戏列表",
        "小游戏列表",
        "装填",
        "开枪",
        "装弹",
        "装弹成语",
        "丢给",
        "骰子",
        "猜数",
        "猜",
        "总游戏开",
        "游戏总开",
        "总游戏关",
        "游戏总关",
        "总游戏状态",
        "游戏总状态",
        "游戏开",
        "游戏关",
        "游戏状态",
        "游戏禁言开",
        "游戏禁言关",
        "游戏禁言状态",
        "清游",
        "确认",
        "取消",
        "转盘榜",
        "转盘总榜",
        "俄罗斯转盘榜单",
        "俄罗斯转盘总榜单",
        "炸弹榜",
        "炸弹总榜",
        "定时炸弹榜单",
        "定时炸弹总榜单",
        "骰子榜",
        "骰子总榜",
        "幸运骰局榜单",
        "幸运骰局总榜单",
        "猜数榜",
        "猜数总榜",
        "猜数字榜单",
        "猜数字总榜单",
        "今日老婆",
        "今日缘分",
        "强取",
        "离婚",
        "解缘",
        "我的缘分",
        "我的老婆",
        "群缘分",
        "群老婆",
        "清缘",
        "确认清缘",
        "取消清缘",
    }

    missing = sorted(name for name in mini_game_commands if f"#{name}" not in catalog)
    assert not missing, f"Update {CATALOG.name} for mini-game commands: {', '.join(missing)}"
