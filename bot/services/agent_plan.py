"""Bounded multi-step planning over the closed skill registry."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bot.application.local_features import FeatureRequest
from bot.services.skills import SkillSpec, registry_loader
from bot.services.tangtang_features import classify_local_feature


MULTI_STEP_MARKERS = ("然后", "再", "接着", "最后", "并且", "还要", "顺便")


@dataclass(frozen=True, slots=True)
class PlanStep:
    action: str
    args: str = ""
    cluster: bool = False
    skill_id: str = ""
    label: str = ""


@dataclass(slots=True)
class AgentPlan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def done(self) -> bool:
        return len(self.completed) + len(self.failed) >= len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {
                    "action": step.action,
                    "args": step.args,
                    "cluster": step.cluster,
                    "skill_id": step.skill_id,
                    "label": step.label,
                }
                for step in self.steps
            ],
            "completed": list(self.completed),
            "failed": list(self.failed),
        }


def needs_plan(text: str) -> bool:
    """Only explicit multi-step requests enter the agent branch."""

    normalized = str(text or "").strip()
    if not normalized:
        return False
    marker_count = sum(1 for marker in MULTI_STEP_MARKERS if marker in normalized)
    if marker_count == 0:
        return False
    return bool(
        classify_local_feature(normalized)
        or "直播" in normalized
        or "排行" in normalized
        or "榜" in normalized
        or "统计" in normalized
        or "记录" in normalized
    )


def _segments(text: str) -> list[str]:
    normalized = str(text or "")
    for marker in MULTI_STEP_MARKERS:
        normalized = normalized.replace(marker, "|")
    return [segment.strip() for segment in normalized.split("|") if segment.strip()]


def _fallback_feature(segment: str) -> FeatureRequest | None:
    """Deterministic fallback for non-ranking local features inside a plan."""

    text = str(segment or "")
    if "直播" not in text and "日程" not in text:
        return None
    if "本周" in text or "这周" in text or "周" in text:
        action = "week_live"
    elif "明日" in text or "明天" in text:
        action = "tomorrow_live"
    elif "枝江" in text:
        action = "zhijiang_schedule"
    else:
        action = "today_live"
    return FeatureRequest(action=action)


def build_plan(text: str) -> AgentPlan:
    """Deterministic plan: each segment must classify to a registered skill."""

    registry = registry_loader.load()
    actions = registry.action_map
    plan = AgentPlan(goal=str(text or "").strip())
    for segment in _segments(text):
        decision = classify_local_feature(segment)
        if decision is not None:
            request = _request_from_decision(decision)
        else:
            request = _fallback_feature(segment)
            if request is None:
                continue
        skill: SkillSpec | None = actions.get(request.action)
        if skill is None:
            continue
        plan.steps.append(
            PlanStep(
                action=request.action,
                args=request.args,
                cluster=request.cluster,
                skill_id=skill.skill_id,
                label=_step_label(request.action, request.args),
            )
        )
    return plan


def _request_from_decision(decision: Any) -> FeatureRequest:
    from bot.application.local_features import request_from_decision

    return request_from_decision(decision)


def _step_label(action: str, args: str) -> str:
    if action == "ranking":
        return f"发言排行 {args or '日'}"
    if action == "zhijiang_schedule":
        return "枝江直播日程"
    if action == "today_live":
        return "今日直播"
    if action == "tomorrow_live":
        return "明日直播"
    if action == "week_live":
        return "本周直播"
    return action


def plan_prompt(plan: AgentPlan) -> str:
    return (
        "你是技能编排器。只能调用下面计划里的已注册技能，不能新增技能、"
        "不能编造执行结果。输出严格 JSON："
        '{"steps":[{"action":"...","args":"...","cluster":false}]}。\n'
        f"目标：{plan.goal}\n"
        f"可用步骤：{json.dumps(plan.to_dict()['steps'], ensure_ascii=False)}"
    )


def parse_plan(text: str) -> list[PlanStep]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("plan did not return JSON")
    payload = json.loads(text[start:end + 1])
    rows = payload.get("steps") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("plan has no steps")
    registry = registry_loader.load()
    steps: list[PlanStep] = []
    for row in rows[:6]:
        if not isinstance(row, Mapping):
            raise ValueError("plan step must be an object")
        action = str(row.get("action") or "")
        skill = registry.action_map.get(action)
        if skill is None:
            raise ValueError(f"plan step is not a registered skill: {action!r}")
        steps.append(
            PlanStep(
                action=action,
                args=str(row.get("args") or ""),
                cluster=bool(row.get("cluster", False)),
                skill_id=skill.skill_id,
                label=_step_label(action, str(row.get("args") or "")),
            )
        )
    return steps
