from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from nonebot import logger

from bot.services.tangtang_chat import TangtangConfig, TangtangProvider


SUPPORTED_ACTIONS = frozenset(
    {
        "zhijiang_schedule",
        "today_live",
        "tomorrow_live",
        "week_live",
        "group_ranking",
        "cluster_ranking",
        "mini_game_roulette",
        "mini_game_bomb",
        "mini_game_dice",
        "mini_game_guess",
        "nte_rank",
        "wuwa_rank",
        "wife_personal",
        "wife_group",
        "denia_gallery",
    }
)
RANKING_ACTIONS = frozenset({"group_ranking", "cluster_ranking"})
RANKING_SCOPES = frozenset({"day", "week", "month", "total"})
MINI_GAME_ACTIONS = frozenset(
    {"mini_game_roulette", "mini_game_bomb", "mini_game_dice", "mini_game_guess"}
)
GAME_RANK_ACTIONS = frozenset({"nte_rank", "wuwa_rank"})

_FEATURE_HINT_RE = re.compile(
    r"直播|在播|有谁在播|谁在播|发言|排行|榜|统计|灌水|集群|日程|枝江|"
    r"A-SOUL|A手|有直播|直播安排|转盘|炸弹|骰子|猜数|猜数字|异环|鸣潮|谁最能聊|谁.*话最多|缘分|老婆|"
    r"美图|自拍|照片|写真|美照",
    re.IGNORECASE,
)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_RANKING_SUBJECT_RE = re.compile(r"(?:发言|灌水)(?:排行(?:榜)?|榜)|发言统计|谁最能聊|谁(?:说的?|说)?话最多")
_EVALUATIVE_RE = re.compile(r"好看|好不好看|有意思|厉害吗|公平|看完|看了|觉得")
_RANKING_REQUEST_RE = re.compile(
    r"告诉我|(?:给我|帮我)?(?:看|看看|看下|看一下|查|查下|查一下|显示|发|来)(?:一?下)?|"
    r"(?:我)?要看|想看"
)

_ROUTER_PERSONA = "你是糖糖的本地功能路由助手，只负责判断呼叫是否要调用机器人本地功能。"

_FUNCTION_TABLE = """支持的功能：
- today_live：今日/今天直播，今天有谁在播
- tomorrow_live：明日/明天直播，明天有人直播吗
- week_live：本周/这周直播日程
- zhijiang_schedule：枝江直播/枝江日程/直播日程
- group_ranking：当前群发言排行，范围 day/week/month/total
- cluster_ranking：当前群所属集群的发言排行，范围 day/week/month/total
- mini_game_roulette/mini_game_bomb/mini_game_dice/mini_game_guess：本群或总的小游戏榜单
- nte_rank：异环（NTE）最强排行，scope 为群或总
- wuwa_rank：鸣潮最强排行，scope 为群或总"""

_ROUTER_RULES = """把呼叫分成三档：
- clear：用户明确要求查看某个功能。
- maybe：用户像在问，但意思已经接近某个功能（例如“今天有直播吗”）。
- chat：只是聊天或评价，没有调用功能的意图。

只输出一个 JSON 对象，不要输出任何其他文字。
clear/maybe 必须同时给出 action、scope、cluster、line：
- action 只能是支持功能里的名字。
- scope 只在发言排行里用：day/week/month/total；其他功能填空字符串。
- cluster 只在当前集群排行里为 true。
- line 是糖糖对用户说的原话，口语自然，1-2 句，不要解释“我帮你调用了什么功能”。
chat 只输出 {"decision":"chat"}。

示例：
- “看一下今天有谁在播？” -> {"decision":"clear","action":"today_live","scope":"","cluster":false,"line":"今天的直播给你找出来啦。"}
- “今天有直播吗” -> {"decision":"maybe","action":"today_live","scope":"","cluster":false,"line":"你要是想看今天有谁直播的话，我给你找找。"}
- “今天直播好看吗” -> {"decision":"chat"}

时间词规则：今天/现在/今天有人播=day；这周/本周=week；这个月/本月=month；总/累计/历史=total。
提到“集群”或当前集群名称的发言榜一律使用 cluster_ranking。
不提供个人发言排行；即使呼叫中出现“我/本人/自己/某人”或 @成员，只要是在请求发言排行，也只能使用群排行。"""

_MINI_GAME_RANK_RE = re.compile(
    r"(转盘|俄罗斯转盘|炸弹|定时炸弹|骰子|幸运骰局|猜数|猜数字)"
    r"(?:的)?(?:总)?(?:排行)?(?:榜|榜单)"
)
_NTE_RANK_RE = re.compile(r"(异环|nte)", re.IGNORECASE)
_WUWA_RANK_RE = re.compile(r"(鸣潮|ww)", re.IGNORECASE)
_GALLERY_REQUEST_RE = re.compile(
    r"想看|要看|看看|看一下|看一张|来一张|来张|来到|来点|发一张|发个|发点|"
    r"给我|要一个|要一张|求一张"
)
_GALLERY_MEDIA_RE = re.compile(r"美图|自拍|照片|写真|美照|好看的图(?:片)?")
_GALLERY_EVALUATION_RE = re.compile(r"(?:好看吗|好不好看|怎么样)$")


def classify_extra_feature(text: str) -> FeatureDecision | None:
    """Deterministic natural-language routing for the second skill batch."""

    normalized = re.sub(r"[\s，,。.!！?？：:、]", "", text)
    if not normalized:
        return None
    match = _MINI_GAME_RANK_RE.search(normalized)
    if match is not None:
        subject = match.group(1)
        total = "总榜" in normalized or "总排行" in normalized or "总榜单" in normalized
        action = {
            "转盘": "mini_game_roulette",
            "俄罗斯转盘": "mini_game_roulette",
            "炸弹": "mini_game_bomb",
            "定时炸弹": "mini_game_bomb",
            "骰子": "mini_game_dice",
            "幸运骰局": "mini_game_dice",
            "猜数": "mini_game_guess",
            "猜数字": "mini_game_guess",
        }[subject]
        scope = "总" if total else "群"
        line = ""
        return FeatureDecision(
            tier="clear", action=action, scope=scope, cluster=False, line=line
        )
    if re.search(r"(?:异环|nte)(?:本群|当前群|群|bot|总)?(?:最强)?排行", normalized, re.I):
        scope = "总" if ("总" in normalized or "bot" in normalized.casefold()) else "群"
        return FeatureDecision(
            tier="clear",
            action="nte_rank",
            scope=scope,
            cluster=False,
            line="",
        )
    if re.search(r"(?:鸣潮|ww)(?:本群|当前群|群|bot|总)?(?:最强)?排行", normalized, re.I):
        scope = "总" if ("总" in normalized or "bot" in normalized.casefold()) else "群"
        return FeatureDecision(
            tier="clear",
            action="wuwa_rank",
            scope=scope,
            cluster=False,
            line="",
        )
    return None


def _extra_feature_line(decision: FeatureDecision, *, call_keyword: str) -> str:
    """Persona-aware opener for the second skill batch."""

    denia = call_keyword != "糖糖"
    if decision.action.startswith("mini_game_"):
        subject = {
            "mini_game_roulette": "转盘",
            "mini_game_bomb": "炸弹",
            "mini_game_dice": "骰子",
            "mini_game_guess": "猜数",
        }.get(decision.action, "这个")
        scope = "总" if decision.scope == "总" else "本群"
        if denia:
            return f"唔……{subject}的{scope}榜，我看看。"
        return f"好呀，这就看看{subject}{scope}榜。"
    if decision.action == "nte_rank":
        if denia:
            return (
                "异环的总榜呀，我翻一下。"
                if decision.scope == "总"
                else "异环的本群榜呀，我翻一下。"
            )
        return (
            "好呀，这就看看异环总最强排行。"
            if decision.scope == "总"
            else "好呀，这就看看异环本群最强排行。"
        )
    if decision.action == "wuwa_rank":
        if denia:
            return (
                "鸣潮的总榜呀，我看看谁最强。"
                if decision.scope == "总"
                else "鸣潮的本群榜呀，我看看谁最强。"
            )
        return (
            "好呀，这就看看鸣潮总榜谁最强。"
            if decision.scope == "总"
            else "好呀，这就看看鸣潮本群谁最强。"
        )
    return ""


_REJECTION_TEXTS = {
    "unknown_skill": {
        "糖糖": (
            "这个糖糖真的不会，也不能现编一个糊弄你。"
            "要不换个糖糖本来就会的？"
        ),
        "denia": (
            "唔……这个我做不到呢，也没法装作能做。"
            "换一件我本来就行的吧？"
        ),
    },
    "group_disabled": {
        "糖糖": "这个功能在本群还没开呢，开起来糖糖就能试。",
        "denia": "这个功能本群还没打开……等它开了我再看。",
    },
    "upstream_unavailable": {
        "糖糖": (
            "糖糖这边连着的服务现在有点不听话，暂时查不了。"
            "等一下再试试。"
        ),
        "denia": "唔，这次没查到，服务暂时没回应。现在还给不了你结果。",
    },
    "invalid_args": {"糖糖": "这个范围糖糖还查不了，换成今天、本周、本月或累计试试吧。", "denia": "唔，这个查询范围还不支持，不能拿别的结果来凑。"},
    "permission": {"糖糖": "这个要有管理权限才能看呢。", "denia": "这项需要管理权限，你现在还不能用呢。"},
    "failed": {"糖糖": "这次没查成功，糖糖还没拿到结果。", "denia": "唔，这次没查成功，还给不了你结果。"},
    "group_required": {"糖糖": "要在对应群里问糖糖，才能查这个哦。", "denia": "这个要在对应的群里问我，才能查呢。"},
    "cluster_required": {"糖糖": "这个群没有加入集群，糖糖只能查本群。", "denia": "这个群没有加入集群，能查的是本群哦。"},
}


def persona_rejection(reason: str, *, call_keyword: str) -> str:
    """Persona-flavoured refusal or unavailability text."""

    texts = _REJECTION_TEXTS.get(str(reason))
    if texts is None:
        texts = _REJECTION_TEXTS["unknown_skill"]
    key = "糖糖" if str(call_keyword) == "糖糖" else "denia"
    return texts[key]


@dataclass(frozen=True, slots=True)
class FeatureDecision:
    tier: str
    action: str
    scope: str
    cluster: bool
    line: str


def has_feature_hint(text: str) -> bool:
    return bool(_FEATURE_HINT_RE.search(text))


def classify_local_feature(
    text: str, *, cluster_labels: Iterable[str] = (), call_keyword: str = "糖糖"
) -> FeatureDecision | None:
    """Resolve explicit feature requests locally while preserving persona feedback."""

    normalized = re.sub(r"[\s，,。.!！?？：:、]", "", text)
    normalized_cluster_labels = {
        re.sub(r"\s+", "", str(label)).casefold()
        for label in cluster_labels
        if str(label).strip()
    }
    if re.search(r"(?:不要|别|不用|不想|不需要).*(?:查|看|发|排行|榜|直播|缘分|老婆|美图|自拍|照片|写真|美照)", normalized):
        return None
    if (
        call_keyword != "糖糖"
        and _GALLERY_MEDIA_RE.search(normalized)
        and _GALLERY_REQUEST_RE.search(normalized)
        and not _GALLERY_EVALUATION_RE.search(normalized)
    ):
        return FeatureDecision("clear", "denia_gallery", "", False, "唔，给你挑一张。")
    if _EVALUATIVE_RE.search(normalized):
        return None
    if re.search(r"昨天|昨日|前天|上周|上个月|上月|去年", normalized):
        return None
    wife_query = re.search(r"看|查|谁|什么|(?:我的|今日)(?:老婆|缘分)$", normalized)
    if wife_query and not re.search(r"抽|强取|离婚|解缘|清空", normalized):
        if re.search(r"(?:群里|本群|群)(?:的|今天的|今日)?(?:缘分|老婆)", normalized):
            return FeatureDecision("clear", "wife_group", "", False, "唔，看看群里的缘分。")
        if re.search(r"(?:我的|我今天的|我今日的|今日|今天的)(?:缘分|老婆)", normalized):
            return FeatureDecision("clear", "wife_personal", "", False, "唔，看看你今天的缘分。")
    if re.search(r"直播|日程|在播", normalized) and re.search(r"看|查|日程|安排|谁|什么|有.*播", normalized):
        if re.search(r"下周|本月|这个月", normalized):
            return None
        action = ("week_live" if re.search(r"本周|这周", normalized) else
                  "tomorrow_live" if re.search(r"明天|明日", normalized) else
                  "zhijiang_schedule" if "枝江" in normalized else "today_live")
        return FeatureDecision("clear", action, "", False, "唔，我看看直播安排。" if call_keyword != "糖糖" else "好呀，这就看看直播安排。")
    extra = classify_extra_feature(normalized)
    if extra is not None:
        return replace(
            extra, line=_extra_feature_line(extra, call_keyword=call_keyword)
        )
    subject = _RANKING_SUBJECT_RE.search(normalized)
    if subject is None:
        return None
    if _EVALUATIVE_RE.search(normalized):
        return None
    before_subject = normalized[: subject.start()]
    after_subject = normalized[subject.end() :]
    requested = subject.group(0).startswith("谁") or bool(_RANKING_REQUEST_RE.search(before_subject)) or bool(
        re.match(r"^(?:给我看|查一下|查下|来一份|来一个|发一下)", after_subject)
    )
    if not requested:
        remainder = normalized.replace(call_keyword, "", 1)
        direct_remainder = re.sub(
            r"(?:我|的|本人|自己|个人|某人|这人|他|她|对方|群里|本群|当前群|"
            r"今天|今日|这周|本周|这个月|本月|总|累计|历史|集群)",
            "",
            remainder,
        )
        for label in normalized_cluster_labels:
            direct_remainder = direct_remainder.casefold().replace(label, "")
        if _RANKING_SUBJECT_RE.fullmatch(direct_remainder) is None:
            return None

    if re.search(r"(?:总|累计|历史)", normalized):
        scope, scope_label = "total", "累计"
    elif re.search(r"(?:这个月|本月|月)", normalized):
        scope, scope_label = "month", "本月"
    elif re.search(r"(?:这周|本周|周)", normalized):
        scope, scope_label = "week", "本周"
    else:
        scope, scope_label = "day", "今天"

    cluster = "集群" in normalized or any(
        label in normalized.casefold() for label in normalized_cluster_labels
    )
    target = "当前集群" if cluster else "群里"
    return FeatureDecision(
        tier="clear",
        action="cluster_ranking" if cluster else "group_ranking",
        scope=scope,
        cluster=cluster,
        line=(f"好呀，糖糖这就看看{target}{scope_label}谁最能聊。" if call_keyword == "糖糖" else f"唔，看看{target}{scope_label}谁最能聊。"),
    )


class TangtangFeatureClassifier:
    def __init__(self, provider: TangtangProvider | None = None) -> None:
        self.provider = provider or TangtangProvider()

    async def classify(
        self,
        config: TangtangConfig,
        text: str,
        *, persona_name: str = "糖糖",
    ) -> tuple[FeatureDecision | None, dict[str, Any]]:
        routing_config = replace(
            config,
            max_output_tokens=min(config.max_output_tokens, 256),
            max_response_chars=min(config.max_response_chars, 800),
        )
        try:
            raw, usage = await self.provider.generate(
                routing_config,
                _ROUTER_PERSONA.replace("糖糖", persona_name),
                self._prompt(text).replace("糖糖对用户", persona_name + "对用户"),
            )
        except Exception as exc:
            logger.warning(
                "Tangtang feature router request failed; falling back to chat: "
                f"{type(exc).__name__}: {exc}"
            )
            return None, {}
        return self._parse(raw), usage

    @staticmethod
    def _prompt(text: str) -> str:
        return (
            f"{_FUNCTION_TABLE}\n\n"
            f"{_ROUTER_RULES}\n\n"
            f"当前呼叫：{text.strip()}"
        )

    @classmethod
    def _parse(cls, raw: str) -> FeatureDecision | None:
        match = _JSON_RE.search(raw)
        if match is None:
            return None
        try:
            data = json.loads(match.group(0))
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None

        decision = str(data.get("decision") or "")
        if decision == "chat":
            return None
        if decision not in {"clear", "maybe"}:
            return None

        action = str(data.get("action") or "")
        if action not in SUPPORTED_ACTIONS:
            return None

        cluster = bool(data.get("cluster", False))
        line = str(data.get("line") or "").strip()
        if not line:
            return None

        scope = str(data.get("scope") or "").strip().lower()
        if action in RANKING_ACTIONS:
            if scope not in RANKING_SCOPES:
                scope = "day"
            if action == "group_ranking" and cluster:
                action = "cluster_ranking"
            if action == "cluster_ranking":
                cluster = True
        elif action in MINI_GAME_ACTIONS or action in GAME_RANK_ACTIONS:
            scope = "总" if scope in {"总", "bot"} else "群"
            cluster = False
        else:
            scope = ""
            cluster = False

        return FeatureDecision(
            tier=decision,
            action=action,
            scope=scope,
            cluster=cluster,
            line=line,
        )
