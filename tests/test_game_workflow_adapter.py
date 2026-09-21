import pytest

import bot.integrations.game_workflow_adapter as adapter
from bot.integrations.game_workflow_adapter import (
    GameWorkflowRejected,
    build_nte_workflow,
    build_wuwa_workflow,
)
from bot.services.nte_rank_data import RankRequest
from bot.services.wuwa_rank_data import WuwaRankRequest


def test_nte_workflows_build_only_reviewed_typed_requests():
    assert build_nte_workflow("nte_help").help_variant == "standard"
    assert build_nte_workflow("nte_rank", scope="group").rank_request == RankRequest(
        None, True, "group", 1
    )
    assert build_nte_workflow(
        "nte_mint_rank", scope="bot", page=3
    ).rank_request == RankRequest("薄荷", False, "bot", 3)


def test_wuwa_workflows_build_only_reviewed_typed_requests():
    assert build_wuwa_workflow("wuwa_help").help_variant == "standard"
    assert build_wuwa_workflow(
        "wuwa_rank", scope="bot"
    ).rank_request == WuwaRankRequest("strongest", None, "bot", 1)
    assert build_wuwa_workflow(
        "wuwa_character_rank", scope="group", character="今汐", page=2
    ).rank_request == WuwaRankRequest("role", "今汐", "group", 2)
    assert build_wuwa_workflow(
        "wuwa_echo_rank", scope="group", character="椿"
    ).rank_request == WuwaRankRequest("phantom", "椿", "group", 1)
    assert build_wuwa_workflow(
        "wuwa_progress_rank", scope="group"
    ).rank_request == WuwaRankRequest("practice", None, "group", 1)


@pytest.mark.parametrize(
    "builder,action,kwargs",
    [
        (build_nte_workflow, "nte_execute", {}),
        (build_nte_workflow, "#nte刷新面板", {}),
        (build_nte_workflow, "nte_rank", {"scope": None}),
        (build_nte_workflow, "nte_mint_rank", {"scope": "group", "page": 1000}),
        (build_wuwa_workflow, "wuwa_execute", {}),
        (build_wuwa_workflow, "#ww登录", {}),
        (build_wuwa_workflow, "wuwa_rank", {"scope": "cluster"}),
        (
            build_wuwa_workflow,
            "wuwa_character_rank",
            {"scope": "group", "character": ""},
        ),
        (
            build_wuwa_workflow,
            "wuwa_progress_rank",
            {"scope": "group", "character": "今汐"},
        ),
    ],
)
def test_arbitrary_or_out_of_contract_workflows_are_rejected(builder, action, kwargs):
    with pytest.raises(GameWorkflowRejected):
        builder(action, **kwargs)


def test_adapter_exposes_no_generic_command_executor():
    assert not hasattr(adapter, "execute_command")
    assert not hasattr(adapter, "run_command")
