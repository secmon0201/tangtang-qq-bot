from __future__ import annotations

from collections import Counter
from pathlib import Path

from bot.services.wuwa_help_catalog import (
    COMPACT_CATEGORIES,
    FULL_CATEGORIES,
    build_compact_catalog,
    build_full_catalog,
    load_upstream_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def _entries(catalog):
    return [row for section in catalog.values() for row in section["data"]]


def _keys(catalog):
    return Counter((row.get("name"), row.get("eg")) for row in _entries(catalog))


def test_compact_help_contains_all_extensions_without_management_commands():
    base, _ = load_upstream_sources(ROOT)
    compact = build_compact_catalog(base)
    assert tuple(compact) == COMPACT_CATEGORIES
    names = {row["name"] for row in _entries(compact)}
    assert {
        "签到",
        "签到日历",
        "自动签到",
        "梭哈",
        "声骸图片评分",
        "国际服分析",
        "邮箱体力提醒",
    } <= names
    reminder = next(row for row in _entries(compact) if row["name"] == "邮箱体力提醒")
    assert "已关闭" in reminder["desc"]
    assert all(not bool(row.get("need_admin")) for row in _entries(compact))


def test_full_help_covers_every_upstream_and_extension_help_entry():
    base, extensions = load_upstream_sources(ROOT)
    full = build_full_catalog(base, extensions)
    assert tuple(full) == FULL_CATEGORIES
    full_keys = _keys(full)
    for source in (base, *extensions.values()):
        for key, count in _keys(source).items():
            assert full_keys[key] >= count, key
    assert full["群管理员功能"]["data"]
    assert full["Bot 主人功能"]["data"]


def test_checked_in_help_resources_match_current_upstream_sources():
    import json

    base, extensions = load_upstream_sources(ROOT)
    compact = json.loads((ROOT / "bot/resources/wuwa_help.json").read_text(encoding="utf-8"))
    full = json.loads((ROOT / "bot/resources/wuwa_help_full.json").read_text(encoding="utf-8"))
    assert compact == build_compact_catalog(base)
    assert full == build_full_catalog(base, extensions)
