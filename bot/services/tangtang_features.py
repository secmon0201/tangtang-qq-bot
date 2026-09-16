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
    }
)
RANKING_ACTIONS = frozenset({"group_ranking", "cluster_ranking"})
RANKING_SCOPES = frozenset({"day", "week", "month", "total"})

_FEATURE_HINT_RE = re.compile(
    r"直播|在播|有谁在播|谁在播|发言|排行|榜|统计|灌水|集群|日程|枝江|"
    r"A-SOUL|A手|有直播|直播安排",
    re.IGNORECASE,
)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_RANKING_SUBJECT_RE = re.compile(r"(?:发言|灌水)(?:排行(?:榜)?|榜)|发言统计")
_EVALUATIVE_RE = re.compile(r"好看吗|好不好看|有意思吗|厉害吗")
_RANKING_REQUEST_RE = re.compile(
    r"(?:给我|帮我)?(?:看|看看|看下|看一下|查|查下|查一下|显示|发|来)(?:一?下)?|"
    r"(?:我)?要看|想看"
)

_ROUTER_PERSONA = "你是糖糖的本地功能路由助手，只负责判断呼叫是否要调用机器人本地功能。"

_FUNCTION_TABLE = """支持的功能：
- today_live：今日/今天直播，今天有谁在播
- tomorrow_live：明日/明天直播，明天有人直播吗
- week_live：本周/这周直播日程
- zhijiang_schedule：枝江直播/枝江日程/直播日程
- group_ranking：当前群发言排行，范围 day/week/month/total
- cluster_ranking：当前群所属集群的发言排行，范围 day/week/month/total"""

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
    subject = _RANKING_SUBJECT_RE.search(normalized)
    if subject is None:
        return None
    if _EVALUATIVE_RE.search(normalized):
        return None
    before_subject = normalized[: subject.start()]
    after_subject = normalized[subject.end() :]
    requested = bool(_RANKING_REQUEST_RE.search(before_subject)) or bool(
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
