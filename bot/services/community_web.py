"""Interactive community web pages and their deterministic QQ PNG captures."""

from __future__ import annotations

import base64
import json
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
GROUP_LABELS = dict(zip(A_COAST_GROUP_IDS, ("修会", "剧团", "莫塔里", "翡萨烈", "墓岛"), strict=True))
ALL_GROUP_KEY = "a-coast"
HELP_GROUP_SPECS = (
    {
        "key": "start",
        "label": "开始使用",
        "title": "先从这里开始",
        "description": "在线帮助适合浏览和跳转；需要把全部指令留在 QQ 里时，也可以继续使用折叠文字版。",
        "tone": "yellow",
        "sources": ("使用说明",),
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
        "key": "activity",
        "label": "活动功能",
        "title": "参加 A海岸活动",
        "description": "从活动大厅找到活动，再查看详情、名单和结果，报名操作仍在 QQ 群内完成。",
        "tone": "orange",
        "sources": ("活动功能",),
        "actions": (),
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
        "title": "A海岸发言统计",
        "description": "复制指令查询本群、五群或个人数据，也可以先打开已经确定的 A海岸排行榜网页。",
        "tone": "green",
        "sources": ("A 海岸发言统计",),
        "actions": ("ranking_day", "ranking_week", "ranking_month", "ranking_total"),
    },
)
HELP_ITEM_ACTIONS = {
    ("直播与日程", "直播日程"): ("live_week",),
    ("直播与日程", "每日直播"): ("live_today", "live_tomorrow"),
    ("A 海岸发言统计", "A 海岸发言排行"): (
        "ranking_day",
        "ranking_week",
        "ranking_month",
        "ranking_total",
    ),
}


def public_web_url(kind: str) -> str | None:
    """Return a bare, configured short link for an approved public web surface."""
    key_name = {"ranking": "qq_ranking", "help": "qq_help"}.get(kind)
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
            "直播与日程",
            "本功能由爱驼提供技术支持",
            [
                ("直播日程", f"{prefix}枝江直播 / {prefix}直播日程 / {prefix}本周直播", "查看本周直播日程。"),
                ("每日直播", f"{prefix}今日直播 / {prefix}明日直播", "查看今天或明天的 A-SOUL 直播日程。"),
            ],
        ),
        (
            "活动功能",
            "",
            [
                ("活动帮助", f"{prefix}活动帮助", "查看活动的专用使用说明。"),
                ("活动查看", f"{prefix}活动大厅 / {prefix}活动详情 <活动ID>", "查看可参与活动及指定活动详情。"),
                ("活动名单", f"{prefix}查看名单 <活动ID>", "查看指定活动的报名名单。"),
                ("活动获奖名单", f"{prefix}获奖名单 <活动ID> / {prefix}查看获奖名单 <活动ID>", "查看抽奖活动的获奖名单。"),
                ("活动参与", f"{prefix}报名 <活动ID> / {prefix}取消报名 <活动ID> / {prefix}我的活动", "报名、取消报名或查看自己的活动。"),
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
                ("总游戏榜单", f"{prefix}转盘总榜 / {prefix}炸弹总榜 / {prefix}骰子总榜 / {prefix}猜数总榜", "查看所有已开启游戏群的榜单。"),
            ],
        ),
    ]
    if stats_enabled:
        categories.append(
            (
                "A 海岸发言统计",
                "",
                [
                    ("当前群发言排行", f"{prefix}发言排行 / {prefix}发言榜 / {prefix}统计 [日/周/月/总]", "查看当前 A 海岸群的发言排行。"),
                    ("A 海岸发言排行", f"{prefix}A海岸发言排行 / {prefix}A海岸发言榜 / {prefix}A海岸统计 [日/周/月/总]", "合并查看五个 A 海岸群的排行。"),
                    ("个人发言统计", f"{prefix}个人发言统计 / {prefix}个人发言榜 / {prefix}个人统计 [QQ号|@成员] [日/周/月/总]", "查看一名成员在五个 A 海岸群的合计和分群折线图；不填成员时查看自己。"),
                    ("发言画像", f"{prefix}发言画像 <QQ号|@成员> / {prefix}画像 <QQ号|@成员>", "生成指定成员的 A 海岸发言画像。"),
                ],
            )
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
) -> dict[str, Any]:
    if scope not in RANKING_SCOPES:
        raise ValueError("scope 仅支持 day、week、month、total")
    group_id: int | None
    if group_key == ALL_GROUP_KEY:
        group_id = None
        group_label = "A海岸"
    else:
        try:
            group_id = int(group_key)
        except ValueError as exc:
            raise ValueError("group 不受支持") from exc
        if group_id not in GROUP_LABELS:
            raise ValueError("group 不受支持")
        group_label = GROUP_LABELS[group_id]
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
    stamp = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m-%d %H:%M")
    if group_total_rows:
        chart = {
            "kind": "group",
            "title": "A海岸五群发言对比",
            "subtitle": f"{SCOPE_LABELS[scope]}统计窗口｜五群发言总数",
            "rows": [
                {
                    "label": str(row.get("group_name") or row.get("group_id") or "未命名群"),
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
            "title": "本群近 7 日发言趋势",
            "subtitle": "固定自然日窗口｜每日发言总数",
            "rows": [
                {
                    "label": str(row.get("day") or "")[-5:],
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
        "scope_options": [{"key": key, "label": SCOPE_LABELS[key]} for key in RANKING_SCOPES],
        "group_options": _group_options(),
        "title": f"{group_label}{SCOPE_TITLES[scope]}",
        "subtitle": "前 100 名｜按发言数降序、QQ 号升序｜记录自 2026-07-28 起",
        "generated_at": stamp,
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
        "subtitle": "选择想做的事，复制 QQ 指令，或直接打开在线功能。",
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
