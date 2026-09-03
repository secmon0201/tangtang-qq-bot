"""Build project help catalogs from the installed Wuthering Waves plugins."""

from __future__ import annotations

import copy
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable


BASE_HELP_RELATIVE = Path(
    "GsUID.Core/gsuid_core/plugins/XutheringWavesUID/"
    "XutheringWavesUID/wutheringwaves_help/help.json"
)
EXTENSION_HELP_RELATIVE = {
    "RoverSign": Path(
        "GsUID.Core/gsuid_core/plugins/RoverSign/"
        "RoverSign/roversign_help/help.json"
    ),
    "TodayEcho": Path(
        "GsUID.Core/gsuid_core/plugins/TodayEcho/"
        "TodayEcho/todayecho_help/help.json"
    ),
    "ScoreEcho": Path(
        "GsUID.Core/gsuid_core/plugins/ScoreEcho/"
        "ScoreEcho/scoreecho_help/help.json"
    ),
}

COMPACT_CATEGORIES = (
    "账号与登录",
    "面板与日常",
    "玩法与排行",
    "抽卡记录",
    "攻略资料",
    "扩展功能",
    "个人设置",
    "帮助",
)
FULL_CATEGORIES = (
    "绑定账号",
    "库街区登录",
    "信息查询",
    "深塔查询",
    "排行查询",
    "抽卡记录",
    "面板图查询",
    "WIKI",
    "个人服务",
    "RoverSign 签到",
    "TodayEcho 梭哈",
    "ScoreEcho 评分分析",
    "RoverReminder 体力提醒",
    "项目排行与帮助",
    "受限面板图管理",
    "群管理员功能",
    "Bot 主人功能",
)


def _entry(name: str, desc: str, example: str, *, admin: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "desc": desc,
        "eg": example,
        "need_ck": False,
        "need_sk": False,
        "need_admin": admin,
    }


EXTENSION_SUMMARY = (
    _entry("签到", "RoverSign 每日签到", "签到"),
    _entry("签到日历", "查看当月签到日历", "签到日历"),
    _entry("自动签到", "开启或关闭每日自动签到", "开启自动签到"),
    _entry("梭哈", "TodayEcho 声骸强化模拟", "梭哈10次"),
    _entry("声骸图片评分", "ScoreEcho 识图评分", "评分 卡提1c(生命)"),
    _entry("国际服分析", "查看 ScoreEcho 分析功能", "分析帮助"),
    _entry("推送邮箱", "设置体力提醒收件邮箱", "推送邮箱 123@qq.com"),
    _entry("体力推送", "开启或关闭体力阈值提醒", "开启体力推送"),
    _entry("推送阈值", "设置 120 至 240 的提醒阈值", "推送阈值 180"),
)

ROVER_REMINDER_ENTRIES = EXTENSION_SUMMARY[-3:]

PROJECT_ENTRIES = (
    _entry("角色评分排行", "本群角色综合评分榜", "今汐评分排行"),
    _entry("角色评分总排行", "机器人角色综合评分总榜", "今汐评分总排行"),
    _entry("角色声骸排行", "本群角色声骸评分榜", "今汐声骸排行"),
    _entry("角色声骸总排行", "机器人角色声骸评分总榜", "今汐声骸总排行"),
    _entry("练度排行", "本群账号练度榜", "练度排行"),
    _entry("练度总排行", "机器人账号练度总榜", "练度总排行"),
    _entry("最强排行", "本群每个角色最高评分", "最强排行"),
    _entry("最强总排行", "机器人每个角色最高评分", "最强总排行"),
    _entry("简约帮助", "普通用户常用功能与扩展", "帮助"),
    _entry("完整帮助", "全部普通、扩展和管理命令", "完整帮助"),
    _entry("原版帮助", "上游原版帮助快照", "原版帮助"),
)


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取鸣潮帮助源 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"鸣潮帮助源不是对象：{path}")
    return data


def load_upstream_sources(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    base = _load(root / BASE_HELP_RELATIVE)
    extensions = {
        name: _load(root / relative)
        for name, relative in EXTENSION_HELP_RELATIVE.items()
    }
    return base, extensions


def _rows(section: dict[str, Any]) -> list[dict[str, Any]]:
    data = section.get("data")
    if not isinstance(data, list):
        raise ValueError("帮助分类缺少 data 列表")
    return [copy.deepcopy(row) for row in data if isinstance(row, dict)]


def _section(desc: str, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"desc": desc, "data": [copy.deepcopy(row) for row in rows]}


def _find(base: dict[str, Any], names: Iterable[str]) -> list[dict[str, Any]]:
    index = {
        str(row.get("name")): row
        for section in base.values()
        if isinstance(section, dict)
        for row in _rows(section)
        if not bool(row.get("need_admin"))
    }
    requested = list(names)
    missing = [name for name in requested if name not in index]
    if missing:
        raise ValueError(f"上游鸣潮帮助缺少简约版条目：{', '.join(missing)}")
    return [copy.deepcopy(index[name]) for name in requested]


def build_compact_catalog(base: dict[str, Any]) -> dict[str, Any]:
    catalog: dict[str, Any] = OrderedDict()
    catalog["账号与登录"] = _section(
        "绑定 UID，登录国服或国际服账号",
        _find(
            base,
            (
                "绑定特征码",
                "切换特征码",
                "删除特征码",
                "查看或刷新特征码列表",
                "删除全部特征码",
                "推荐->登录页登录",
                "国际服登录",
                "添加token",
                "删除token",
                "获取绑定的token",
            ),
        ),
    )
    catalog["面板与日常"] = _section(
        "面板、养成、体力和日常查询",
        _find(
            base,
            (
                "鸣潮面板更新",
                "练度统计",
                "基本信息卡片",
                "收藏图鉴",
                "查询声骸列表",
                "查询角色面板",
                "查询角色伤害",
                "查询角色权重",
                "查看优化建议",
                "体力",
                "查询探索度",
                "库洛币",
                "日历",
                "兑换码",
                "角色养成",
                "角色持有率",
            ),
        ),
    )
    gameplay = _find(
        base,
        (
            "深塔",
            "全息战略",
            "深塔出场率",
            "冥歌海墟",
            "冥歌海墟出场率",
            "终焉矩阵",
            "矩阵出场率",
            "海墟无尽排行",
            "海墟无尽总排行",
            "矩阵群排行",
            "矩阵总排行",
        ),
    )
    catalog["玩法与排行"] = _section(
        "战绩查询；项目榜单每页 100 条",
        [*gameplay, *PROJECT_ENTRIES[:8]],
    )
    catalog["抽卡记录"] = _section(
        "导入、查询、排行、导出与删除记录",
        _rows(base["抽卡记录"]),
    )
    catalog["攻略资料"] = _section(
        "角色、武器、声骸和游戏公告",
        _find(
            base,
            (
                "角色攻略",
                "角色共鸣链",
                "角色技能",
                "角色机制",
                "角色别名",
                "武器介绍",
                "声骸介绍",
                "武器列表",
                "套装效果",
                "深塔海墟信息",
                "游戏公告列表",
                "游戏公告明细",
                "综合评分说明",
            ),
        ),
    )
    catalog["扩展功能"] = _section(
        "RoverSign、TodayEcho、ScoreEcho、RoverReminder",
        EXTENSION_SUMMARY,
    )
    catalog["个人设置"] = _section(
        "背景、面板图、语言与 UID 显示",
        _rows(base["个人服务"]),
    )
    catalog["帮助"] = _section("简约版、完整版与上游快照", PROJECT_ENTRIES[-3:])
    return catalog


def build_full_catalog(
    base: dict[str, Any], extensions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    catalog: dict[str, Any] = OrderedDict()
    restricted_panel_rows: list[dict[str, Any]] = []
    group_admin_rows: list[dict[str, Any]] = []
    owner_rows: list[dict[str, Any]] = []

    for category, source_section in base.items():
        if not isinstance(source_section, dict):
            continue
        rows = _rows(source_section)
        if category == "群管理员功能":
            group_admin_rows.extend(rows)
            continue
        if category == "bot主人功能":
            owner_rows.extend(rows)
            continue
        if category == "面板图帮助":
            public_rows = [row for row in rows if not bool(row.get("need_admin"))]
            restricted_panel_rows.extend(row for row in rows if bool(row.get("need_admin")))
            catalog["面板图查询"] = _section(str(source_section.get("desc") or "面板图查询"), public_rows)
            continue
        catalog[category] = _section(str(source_section.get("desc") or category), rows)

    sign_rows: list[dict[str, Any]] = []
    for section in extensions["RoverSign"].values():
        if not isinstance(section, dict):
            continue
        for row in _rows(section):
            if bool(row.get("need_admin")):
                owner_rows.append(row)
            else:
                sign_rows.append(row)
    sign_rows.insert(1, copy.deepcopy(EXTENSION_SUMMARY[1]))
    catalog["RoverSign 签到"] = _section("签到、签到日历与自动签到", sign_rows)

    today_rows = [
        row
        for section in extensions["TodayEcho"].values()
        if isinstance(section, dict)
        for row in _rows(section)
    ]
    catalog["TodayEcho 梭哈"] = _section("声骸强化模拟与历史结果", today_rows)

    score_rows = [copy.deepcopy(EXTENSION_SUMMARY[4])]
    score_rows.extend(
        row
        for section in extensions["ScoreEcho"].values()
        if isinstance(section, dict)
        for row in _rows(section)
    )
    catalog["ScoreEcho 评分分析"] = _section("图片评分与国际服独立面板分析", score_rows)
    catalog["RoverReminder 体力提醒"] = _section(
        "邮件、推送开关与体力阈值",
        ROVER_REMINDER_ENTRIES,
    )
    catalog["项目排行与帮助"] = _section(
        "只改变排行图片和帮助页；其他命令继续由上游处理",
        PROJECT_ENTRIES,
    )
    catalog["受限面板图管理"] = _section("需要上游权限校验的面板图管理命令", restricted_panel_rows)
    catalog["群管理员功能"] = _section("仅当前群群主或管理员可执行", group_admin_rows)
    catalog["Bot 主人功能"] = _section("仅 Core 配置的 Bot 主人可执行", owner_rows)
    return catalog


__all__ = [
    "BASE_HELP_RELATIVE",
    "COMPACT_CATEGORIES",
    "EXTENSION_HELP_RELATIVE",
    "FULL_CATEGORIES",
    "build_compact_catalog",
    "build_full_catalog",
    "load_upstream_sources",
]
