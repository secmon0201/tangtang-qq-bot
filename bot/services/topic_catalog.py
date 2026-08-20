from __future__ import annotations

import json

from bot.config import ROOT


# Keep a fallback snapshot so the AI topic gate still works in a packaged
# deployment where the optional GenshinUID resource directory is absent.
MINGCHAO_CHARACTER_NAMES_FALLBACK: tuple[str, ...] = (
    "丹瑾", "丽贝卡", "仇远", "今汐", "凌阳", "千咲", "卜灵", "卡卡罗",
    "卡提希娅", "吟霖", "嘉贝莉娜", "坎特蕾拉", "夏空", "奥古斯塔", "守岸人",
    "安可", "尤诺", "布兰特", "弗洛洛", "忌炎", "折枝", "散华", "桃祈", "椿",
    "洛可可", "洛瑟菈", "渊武", "漂泊者·导电", "漂泊者·气动", "漂泊者·湮灭",
    "漂泊者·衍射", "灯灯", "炽霞", "爱弥斯", "珂莱塔", "琳奈", "白芷", "相里要",
    "秋水", "秧秧", "秧秧·玄翎", "穗穗", "绯雪", "维里奈", "莫宁", "莫特斐",
    "菲比", "西格莉卡", "赞妮", "达妮娅", "釉瑚", "鉴心", "长离", "陆·赫斯",
    "露帕", "露西",
)

MINGCHAO_ALIAS_PATH = (
    ROOT
    / "GsUID.Core"
    / "data"
    / "XutheringWavesUID"
    / "resource"
    / "map"
    / "alias"
    / "char_alias.json"
)


def _load_mingchao_character_names() -> tuple[str, ...]:
    """Use the local XutheringWavesUID character catalog when available."""

    names = set(MINGCHAO_CHARACTER_NAMES_FALLBACK)
    try:
        data = json.loads(MINGCHAO_ALIAS_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        names.update(str(name).strip() for name in data if str(name).strip())
    return tuple(sorted(names, key=lambda name: (-len(name), name)))


MINGCHAO_CHARACTER_NAMES = _load_mingchao_character_names()

# Short English codes are included because they are common in QQ chat, but the
# AI matcher treats them as standalone tokens to avoid matching ordinary words.
MINGCHAO_TOPIC_KEYWORDS = (
    "鸣潮",
    "wuthering waves",
    "wuwa",
    "ww",
    "mc",
    *MINGCHAO_CHARACTER_NAMES,
)
