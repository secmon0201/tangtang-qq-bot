"""Interactive community web pages and their deterministic QQ PNG captures."""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

from bot.config import A_COAST_GROUP_IDS, RESOURCE_DIR, ROOT, settings
from bot.services.web_screenshot import LocalWebScreenshotRenderer


WEB_PAGE_PATH = RESOURCE_DIR / "community_web.html"
SHORT_LINK_CONFIG_PATH = ROOT / "config" / "public-short-links.json"
RANKING_SCOPES = ("day", "week", "month", "total")
SCOPE_LABELS = {"day": "日榜", "week": "周榜", "month": "月榜", "total": "总榜"}
SCOPE_TITLES = {"day": "今日发言榜", "week": "本周发言榜", "month": "本月发言榜", "total": "累计发言榜"}
SCOPE_KICKERS = {
    "day": "AK-BOT FUNCTION",
    "week": "AK-BOT FUNCTION",
    "month": "AK-BOT FUNCTION",
    "total": "AK-BOT FUNCTION",
}
GROUP_LABELS = dict(zip(A_COAST_GROUP_IDS, ("修会", "剧团", "莫塔里", "翡萨烈", "墓岛"), strict=True))
DOMAIN_GROUP_KEY = "domain"
ALL_GROUP_KEY = DOMAIN_GROUP_KEY
HELP_GROUP_SPECS = (
    {
        "key": "start",
        "label": "快速开始",
        "title": "先从常用入口开始",
        "description": "这里保留群友最常用的入口：在线帮助与折叠文字帮助。",
        "tone": "pink",
        "sources": ("使用说明",),
        "actions": (),
    },
    {
        "key": "chat",
        "label": "聊天互动",
        "title": "叫糖糖与群内互动",
        "description": "被呼叫会话和主动聊天由本群开关决定；群友只需要在群里自然呼叫或 @ 糖糖。",
        "tone": "violet",
        "sources": ("聊天互动",),
        "actions": (),
    },
    {
        "key": "nte",
        "label": "NTE",
        "title": "异环 NTE 查询与排行",
        "description": "NTE 默认看当前群；写出“总排行”才会查看糖糖记录到的全部群排行。",
        "tone": "mint",
        "sources": ("NTE 查询与排行",),
        "actions": (),
    },
    {
        "key": "wuwa",
        "label": "鸣潮",
        "title": "鸣潮查询与本地排行",
        "description": "鸣潮默认看当前群；写出“总排行”才会读取本机器人保存的全部本地数据。",
        "tone": "pink",
        "sources": ("鸣潮查询与排行",),
        "actions": (),
    },
    {
        "key": "live",
        "label": "直播日程",
        "title": "枝江直播与日程",
        "description": "既可以复制传统 QQ 指令，也可以直接进入今日、明日或本周直播网页。",
        "tone": "pink",
        "sources": ("直播与日程",),
        "actions": ("live_today", "live_tomorrow", "live_week"),
    },
    {
        "key": "fate",
        "label": "今日缘分",
        "title": "今日缘分与关系档案",
        "description": "领取关系后由后台自动演算三幕故事，个人与群档案负责回看变化。",
        "tone": "pink-soft",
        "sources": ("今日老婆",),
        "actions": (),
    },
    {
        "key": "games",
        "label": "本地小游戏",
        "title": "本地小游戏",
        "description": "玩法入口和对应榜单属于同一个完整大类，可在一页内查看全部规则入口。",
        "tone": "cyan",
        "sources": ("小游戏", "小游戏榜单"),
        "actions": (),
    },
    {
        "key": "stats",
        "label": "发言统计",
        "title": "群发言统计",
        "description": "普通排行只查询当前群；加入集群的群还可以查看当前集群排行。",
        "tone": "blue",
        "sources": ("发言统计",),
        "actions": (),
    },
    {
        "key": "group-admin",
        "label": "本群设置",
        "title": "群管理员的本群设置",
        "description": "群主和 QQ 群管理员只维护当前群：功能开关、群代称和本群过滤，不涉及全局系统设置。",
        "tone": "gold",
        "sources": ("本群设置", "群内自动功能"),
        "actions": (),
    },
)
HELP_ITEM_ACTIONS = {
    ("直播与日程", "直播日程"): ("live_week",),
    ("直播与日程", "每日直播"): ("live_today", "live_tomorrow"),
}


def public_web_url(kind: str) -> str | None:
    """Return a bare, configured short link for an approved public web surface."""
    key_name = {"help": "qq_help"}.get(kind)
    if key_name is None:
        return None
    try:
        config = json.loads(SHORT_LINK_CONFIG_PATH.read_text(encoding="utf-8"))
        host = str(config["host"])
        code = str(config[key_name])
        if code not in config["links"]:
            return None
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return f"{host}/{code}"


def public_domain_ranking_url(token: str) -> str | None:
    """Build a long-lived bearer link without putting the token in logs or config."""

    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", str(token)):
        return None
    return f"https://tangtang.secmon.cn/ranking/{token}/"


def _help_action_index() -> dict[str, dict[str, str]]:
    """Load only HTTPS actions owned by the public Tangtang site."""
    try:
        config = json.loads(SHORT_LINK_CONFIG_PATH.read_text(encoding="utf-8"))
        raw_actions = config.get("help_actions", {})
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    actions: dict[str, dict[str, str]] = {}
    if not isinstance(raw_actions, dict):
        return actions
    for key, raw_action in raw_actions.items():
        if not isinstance(key, str) or not isinstance(raw_action, dict):
            continue
        label = raw_action.get("label")
        target = raw_action.get("target")
        if not isinstance(label, str) or not isinstance(target, str):
            continue
        parsed = urlsplit(target)
        if parsed.scheme != "https" or parsed.hostname != "tangtang.secmon.cn":
            continue
        actions[key] = {"key": key, "label": label, "target": target}
    return actions


def public_help_categories(
    prefix: str,
    *,
    stats_enabled: bool,
) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """One public command catalog shared by the interactive help page and text help."""
    categories = [
        (
            "使用说明",
            "",
            [
                ("在线帮助", f"{prefix}帮助", "打开可切换的普通用户功能说明。"),
                ("折叠文字帮助", f"{prefix}帮助文字", "以合并转发发送可复制指令，不刷屏。"),
            ],
        ),
        (
            "聊天互动",
            "",
            [
                ("呼叫糖糖", "@糖糖 帮我看看今天能玩什么\n糖糖 帮我查一下异环怎么登录", "被呼叫会话开启后，可用自然语言向糖糖询问公开功能、玩法和本群信息。"),
                ("群内主动聊天", "普通群聊自然发言", "主动聊天开启后，糖糖会按本群节奏参与普通聊天；群友不需要额外指令。"),
            ],
        ),
        (
            "NTE 查询与排行",
            "",
            [
                ("异环帮助", f"{prefix}nte帮助 / nte帮助", "查看本地新版异环帮助图；NTE 前缀支持 #NTE、NTE、#nte、nte。"),
                ("登录与查询", f"{prefix}nte登录 / {prefix}nte查询 / {prefix}nte刷新面板", "绑定塔吉多账号后查询异环角色、面板和进度。"),
                ("当前群排行", f"{prefix}nte薄荷排行 / {prefix}nte最强排行", "查看当前群的角色评分排行和各角色最强排行。"),
                ("机器人总排行", f"{prefix}nte薄荷总排行 / {prefix}nte最强总排行", "显式写出总排行时，才查看糖糖记录到的全部群 NTE 排行。"),
            ],
        ),
        (
            "鸣潮查询与排行",
            "排行只读取本机绑定和面板缓存，不调用上游公共总榜。",
            [
                ("鸣潮帮助", f"{prefix}ww帮助 / {prefix}ww完整帮助", "简约版列常用功能与扩展；完整版列全部普通及管理命令。"),
                ("登录与查询", f"{prefix}ww登录 / {prefix}ww刷新面板 / {prefix}ww体力", "登录与普通查询继续交给 XutheringWavesUID。"),
                ("当前群排行", f"{prefix}ww今汐评分排行 / {prefix}ww今汐声骸排行 / {prefix}ww练度排行", "按本群鸣潮绑定生成项目榜单，每页 100 条。"),
                ("机器人总排行", f"{prefix}ww今汐评分总排行 / {prefix}ww今汐声骸总排行 / {prefix}ww练度总排行", "显式写出总排行时，才查看本机器人全部本地绑定。"),
            ],
        ),
        (
            "直播与日程",
            "本功能由爱驼提供技术支持",
            [
                ("直播日程", f"{prefix}枝江直播 / {prefix}直播日程 / {prefix}本周直播", "查看本周直播日程。"),
                ("每日直播", f"{prefix}今日直播 / {prefix}明日直播", "查看今天或明天的 A-SOUL 直播日程。"),
            ],
        ),
        (
            "今日老婆",
            "",
            [
                ("今日缘分", f"{prefix}今日老婆 / {prefix}今日缘分 / {prefix}强取 @群友", "随机或定向领取今日关系，后续互动由后台自动演算。"),
                ("缘分档案", f"{prefix}我的缘分 / {prefix}群缘分 / {prefix}群缘分 历史 / {prefix}离婚", "查看当前关系、永久个人留档、群内往日摘要，或结束今日关系。"),
            ],
        ),
        (
            "小游戏",
            "",
            [
                ("小游戏菜单", f"{prefix}游戏列表 / {prefix}小游戏列表", "查看小游戏玩法和入口。"),
                ("俄罗斯转盘", f"{prefix}装填 / {prefix}开枪", "发起或进行俄罗斯转盘。"),
                ("定时炸弹", f"{prefix}装弹 / {prefix}丢给 @成员", "发起或传递定时炸弹。"),
                ("成语炸弹", f"{prefix}装弹成语 [专业/娱乐] [60-600]\n四字词 {prefix}丢给 @成员", "发起或传递成语接龙炸弹。"),
                ("幸运骰局", f"{prefix}骰子", "发起一局幸运骰局。"),
                ("猜数字", f"{prefix}猜数 / {prefix}猜 <0-999>", "发起猜数字或提交猜测。"),
            ],
        ),
        (
            "小游戏榜单",
            "",
            [
                ("群游戏榜单", f"{prefix}转盘榜 / {prefix}炸弹榜 / {prefix}骰子榜 / {prefix}猜数榜", "查看当前群游戏榜单。"),
                ("域游戏榜单", f"{prefix}转盘总榜 / {prefix}炸弹总榜 / {prefix}骰子总榜 / {prefix}猜数总榜", "独群仍只统计本群；集群统计当前集群。"),
            ],
        ),
    ]
    if stats_enabled:
        categories.append(
            (
                "发言统计",
                "",
                [
                    ("当前群发言排行", f"{prefix}发言排行 / {prefix}发言榜 / {prefix}统计 [日/周/月/总]", "查看当前群的发言排行。"),
                    ("当前集群发言排行", f"{prefix}集群发言排行 / {prefix}集群发言榜 / {prefix}集群统计 [日/周/月/总]", "仅集群内群可合并查看当前集群排行。"),
                    ("发言档案", f"{prefix}发言记录 / {prefix}发言搜索 / {prefix}发言画像 <QQ号|@成员>", "记录与搜索限定当前群；画像按当前域生成。"),
                ],
            )
        )
    categories.extend(
        [
            (
                "本群设置",
                "仅本群群主和 QQ 群管理员可修改；普通群友可查看已开放功能。",
                [
                    ("本群功能状态", f"{prefix}群设置 / {prefix}本群设置", "普通群友可查看当前已开放功能；群管理员会看到可维护的本群开关。"),
                    ("本群功能开关", f"{prefix}群设置 <功能> 开|关\n{prefix}开关小游戏 / {prefix}开关今日老婆 / {prefix}开关准时报点\n{prefix}开关被呼叫会话 / {prefix}开关B站推送 / {prefix}开关被动互动\n{prefix}开关NTE / {prefix}开关鸣潮", "这些开关只影响当前群，不改变其他群，也不改变集群统计成员关系。"),
                    ("本群代称与过滤", f"{prefix}群设置 代称 <名称>\n{prefix}群设置 过滤 列表\n{prefix}群设置 过滤 添加 QQ号\n{prefix}群设置 过滤 移除 QQ号", "群代称用于当前群展示；本群过滤名单只作用于当前群。"),
                ],
            ),
            (
                "群内自动功能",
                "",
                [
                    ("准时报点", f"{prefix}群设置 准时报点 开|关", "群管理员可决定当前群是否接收准时报点；具体全局运行时段不在公开帮助中展开。"),
                    ("被动互动", f"{prefix}群设置 被动互动 开|关", "群管理员可决定当前群是否开启表情、复读等被动互动；具体概率参数不在公开帮助中展开。"),
                    ("B站推送", f"{prefix}群设置 B站推送 开|关", "群管理员可决定当前群是否接收已配置的 B 站相关推送。"),
                ],
            ),
        ]
    )
    return categories


def _group_options() -> list[dict[str, str]]:
    return [{"key": ALL_GROUP_KEY, "label": "A海岸"}] + [
        {"key": str(group_id), "label": GROUP_LABELS[group_id]}
        for group_id in A_COAST_GROUP_IDS
    ]


def _avatar_data_uri(path: Path | None, size: int = 80) -> str:
    """Embed a bounded local avatar so screenshots and the public page share one payload."""
    if path is None or not path.is_file():
        return ""
    try:
        with Image.open(path) as source:
            image = ImageOps.fit(source.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="WEBP", quality=82, method=4)
    except (OSError, ValueError):
        return ""
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/webp;base64,{encoded}"


def ranking_payload(
    stats_service: Any,
    scope: str,
    group_key: str = ALL_GROUP_KEY,
    *,
    rows: Iterable[Mapping[str, Any]] | None = None,
    avatar_paths: Mapping[int, Path] | None = None,
    group_totals: Iterable[Mapping[str, Any]] | None = None,
    group_avatar_paths: Mapping[int, Path] | None = None,
    daily_totals: Iterable[Mapping[str, Any]] | None = None,
    selected_group_id: int | None = None,
    group_label_override: str | None = None,
    group_labels: Mapping[int, str] | None = None,
    group_options: Iterable[Mapping[str, str]] | None = None,
    history_since: str | None = None,
) -> dict[str, Any]:
    if scope not in RANKING_SCOPES:
        raise ValueError("scope 仅支持 day、week、month、total")
    group_id: int | None
    if selected_group_id is not None:
        group_id = int(selected_group_id)
        group_label = group_label_override or str(group_id)
    elif group_key in {ALL_GROUP_KEY, DOMAIN_GROUP_KEY}:
        group_id = None
        group_label = group_label_override or "A海岸"
    else:
        try:
            group_id = int(group_key)
        except ValueError as exc:
            raise ValueError("group 不受支持") from exc
        if group_id not in GROUP_LABELS:
            raise ValueError("group 不受支持")
        group_label = group_label_override or GROUP_LABELS[group_id]
    ranking_rows = (
        [dict(row) for row in rows]
        if rows is not None
        else [dict(row) for row in stats_service.ranking_rows(scope, group_id)]
    )[:100]
    if group_totals is None:
        group_total_rows = (
            [dict(row) for row in stats_service.group_totals(scope)]
            if group_id is None
            else []
        )
    else:
        group_total_rows = [dict(row) for row in group_totals]
    if daily_totals is None:
        daily_total_rows = (
            [dict(row) for row in stats_service.recent_group_daily_totals(group_id)]
            if group_id is not None
            else []
        )
    else:
        daily_total_rows = [dict(row) for row in daily_totals]
    row_avatar_paths = avatar_paths or {}
    chart_avatar_paths = group_avatar_paths or {}
    total = sum(int(row.get("message_count") or 0) for row in ranking_rows)
    generated = datetime.now(ZoneInfo(settings.timezone))
    stamp = generated.strftime("%Y-%m-%d %H:%M")
    if group_total_rows:
        chart = {
            "kind": "group",
            "title": f"{group_label}群组发言对比",
            "subtitle": f"{SCOPE_LABELS[scope]}统计窗口｜成员群发言总数",
            "x_axis_label": "群组",
            "y_axis_label": "发言数（条）",
            "rows": [
                {
                    "label": (group_labels or GROUP_LABELS).get(
                        int(row.get("group_id") or 0),
                        str(row.get("group_name") or row.get("group_id") or "未命名群"),
                    ),
                    "message_count": int(row.get("message_count") or 0),
                    "avatar": _avatar_data_uri(
                        chart_avatar_paths.get(int(row.get("group_id") or 0)), size=56
                    ),
                }
                for row in group_total_rows
            ],
        }
    elif daily_total_rows:
        chart = {
            "kind": "daily",
            "title": f"{group_label}近 7 日发言趋势",
            "subtitle": "固定自然日窗口｜每日发言总数",
            "x_axis_label": "日期",
            "y_axis_label": "发言数（条）",
            "rows": [
                {
                    "label": str(row.get("day") or "")[-5:].replace("-", "."),
                    "message_count": int(row.get("message_count") or 0),
                    "avatar": "",
                }
                for row in daily_total_rows
            ],
        }
    else:
        chart = None
    return {
        "mode": "ranking",
        "scope": scope,
        "group": group_key,
        "group_label": group_label,
        "scope_title": SCOPE_TITLES[scope],
        "header_kicker": SCOPE_KICKERS[scope],
        "scope_options": [{"key": key, "label": SCOPE_LABELS[key]} for key in RANKING_SCOPES],
        "group_options": [dict(option) for option in group_options] if group_options is not None else _group_options(),
        "title": f"{group_label}{SCOPE_TITLES[scope]}",
        "subtitle": (
            f"前 100 名｜按发言数降序、QQ 号升序｜历史数据最早自 {history_since}"
            if history_since
            else "前 100 名｜按发言数降序、QQ 号升序｜历史数据以糖糖加入本群后记录为准"
        ),
        "generated_at": stamp,
        "generated_date": generated.strftime("%Y.%m.%d"),
        "generated_month": generated.strftime("%m"),
        "generated_day": generated.strftime("%d"),
        "displayed_count": len(ranking_rows),
        "message_total": total,
        "rows": [
            {
                "rank": int(row.get("rank") or index),
                "nickname": str(row.get("nickname") or "未命名成员"),
                "message_count": int(row.get("message_count") or 0),
                "group_name": str(row.get("group_labels") or row.get("group_name") or ""),
                "avatar": _avatar_data_uri(
                    row_avatar_paths.get(int(row.get("user_id") or 0))
                ),
            }
            for index, row in enumerate(ranking_rows, start=1)
        ],
        "chart": chart,
    }


def help_payload() -> dict[str, Any]:
    action_index = _help_action_index()
    categories = [
        {
            "title": title,
            "note": note,
            "items": [
                {
                    "title": item_title,
                    "command": command,
                    "description": description,
                    "actions": [
                        action_index[action_key]
                        for action_key in HELP_ITEM_ACTIONS.get((title, item_title), ())
                        if action_key in action_index
                    ],
                }
                for item_title, command, description in items
            ],
        }
        for title, note, items in public_help_categories(
            settings.command_prefix, stats_enabled=settings.stats_realtime_enabled
        )
    ]
    categories_by_title = {category["title"]: category for category in categories}
    groups = []
    for spec in HELP_GROUP_SPECS:
        sections = [
            categories_by_title[source]
            for source in spec["sources"]
            if source in categories_by_title
        ]
        if not sections:
            continue
        quick_actions = [
            action_index[action_key]
            for action_key in spec["actions"]
            if action_key in action_index
        ]
        groups.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "title": spec["title"],
                "description": spec["description"],
                "tone": spec["tone"],
                "sections": sections,
                "quick_actions": quick_actions,
                "item_count": sum(len(section["items"]) for section in sections),
            }
        )
    return {
        "mode": "help",
        "title": "糖糖在线帮助",
        "subtitle": "按群友、游戏、统计和本群管理场景浏览；复制后回到 QQ 发送即可。",
        "categories": categories,
        "groups": groups,
    }


def page_html(mode: str, payload: Mapping[str, Any] | None = None, *, capture: bool = False) -> str:
    source = WEB_PAGE_PATH.read_text(encoding="utf-8")
    serialized = "null" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return (
        source.replace("__COMMUNITY_MODE__", mode)
        .replace("__COMMUNITY_PAYLOAD_JSON__", serialized)
        .replace("__COMMUNITY_CAPTURE_CLASS__", "capture-mode" if capture else "")
    )


class CommunityWebRenderer(LocalWebScreenshotRenderer):
    """Render the same public ranking surface that users can continue using online."""

    def __init__(self, output_dir: Path) -> None:
        super().__init__(output_dir, "community_html")

    async def render_ranking(self, payload: Mapping[str, Any]) -> Path:
        return await self.render_html(page_html("ranking", payload, capture=True), "ranking")
