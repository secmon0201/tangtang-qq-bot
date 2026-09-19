"""Agent branch: deterministic multi-step planning over registered skills."""
from __future__ import annotations

import pytest

from bot.services.agent_plan import (
    AgentPlan,
    build_plan,
    needs_plan,
    parse_plan,
)


def test_single_skill_request_does_not_enter_agent_branch():
    assert not needs_plan("看一下今天的发言排行")
    assert not needs_plan("今天有直播吗")


def test_multi_step_marker_enters_agent_branch():
    assert needs_plan("先看今天的发言排行，然后看本周直播日程")


def test_build_plan_orders_registered_skill_steps():
    plan = build_plan("先看今天的发言排行，然后看本周直播日程")
    assert len(plan.steps) == 2
    assert plan.steps[0].action == "ranking"
    assert plan.steps[0].args == "日"
    assert plan.steps[0].skill_id == "commands"
    assert plan.steps[1].action == "week_live"
    assert plan.steps[1].skill_id == "asoul"
    assert not plan.done


def test_plan_dict_round_trip_fields():
    plan = build_plan("先看今天的发言排行，然后看本周直播日程")
    payload = plan.to_dict()
    assert payload["goal"]
    assert [step["action"] for step in payload["steps"]] == ["ranking", "week_live"]


def test_parse_plan_rejects_unregistered_skill():
    with pytest.raises(ValueError):
        parse_plan('{"steps":[{"action":"invented_skill"}]}')


def test_parse_plan_accepts_registered_skill():
    steps = parse_plan('{"steps":[{"action":"ranking","args":"周"}]}')
    assert steps[0].skill_id == "commands"
    assert steps[0].label == "发言排行 周"


def test_plan_without_steps_is_not_ready():
    plan = AgentPlan(goal="空计划")
    assert plan.done
