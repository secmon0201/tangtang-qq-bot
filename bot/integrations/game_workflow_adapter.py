"""Typed, allowlisted Agent workflows for project-owned NTE and Wuwa views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from bot.services.nte_rank_data import RankRequest
from bot.services.wuwa_rank_data import WuwaRankRequest


Scope = Literal["group", "bot"]
HelpVariant = Literal["standard"]

NTE_WORKFLOW_ACTIONS = frozenset({"nte_help", "nte_rank", "nte_mint_rank"})
WUWA_WORKFLOW_ACTIONS = frozenset({
    "wuwa_help",
    "wuwa_rank",
    "wuwa_character_rank",
    "wuwa_echo_rank",
    "wuwa_progress_rank",
})


class GameWorkflowRejected(ValueError):
    """The requested operation is outside the reviewed project-owned surface."""


@dataclass(frozen=True, slots=True)
class NTEWorkflow:
    action: str
    help_variant: HelpVariant | None = None
    rank_request: RankRequest | None = None


@dataclass(frozen=True, slots=True)
class WuwaWorkflow:
    action: str
    help_variant: HelpVariant | None = None
    rank_request: WuwaRankRequest | None = None


def build_nte_workflow(
    action: str,
    *,
    scope: Scope | None = None,
    page: int = 1,
) -> NTEWorkflow:
    """Build one reviewed NTE workflow without accepting a command string."""

    if action not in NTE_WORKFLOW_ACTIONS:
        raise GameWorkflowRejected(f"unsupported NTE workflow: {action}")
    if action == "nte_help":
        if scope is not None or page != 1:
            raise GameWorkflowRejected("NTE help does not accept rank arguments")
        return NTEWorkflow(action, help_variant="standard")
    checked_scope = _scope(scope)
    checked_page = _page(page)
    return NTEWorkflow(
        action,
        rank_request=RankRequest(
            None if action == "nte_rank" else "薄荷",
            action == "nte_rank",
            checked_scope,
            checked_page,
        ),
    )


def build_wuwa_workflow(
    action: str,
    *,
    scope: Scope | None = None,
    page: int = 1,
    character: str | None = None,
) -> WuwaWorkflow:
    """Build one reviewed Wuwa workflow without accepting a command string."""

    if action not in WUWA_WORKFLOW_ACTIONS:
        raise GameWorkflowRejected(f"unsupported Wuwa workflow: {action}")
    if action == "wuwa_help":
        if scope is not None or page != 1 or character is not None:
            raise GameWorkflowRejected("Wuwa help does not accept rank arguments")
        return WuwaWorkflow(action, help_variant="standard")

    checked_scope = _scope(scope)
    checked_page = _page(page)
    kinds: dict[str, Literal["role", "phantom", "practice", "strongest"]] = {
        "wuwa_rank": "strongest",
        "wuwa_character_rank": "role",
        "wuwa_echo_rank": "phantom",
        "wuwa_progress_rank": "practice",
    }
    kind = kinds[action]
    checked_character: str | None = None
    if kind in {"role", "phantom"}:
        checked_character = str(character or "").strip()
        if not 1 <= len(checked_character) <= 24:
            raise GameWorkflowRejected("Wuwa character must contain 1 to 24 characters")
    elif character is not None:
        raise GameWorkflowRejected(f"{action} does not accept a character")
    return WuwaWorkflow(
        action,
        rank_request=WuwaRankRequest(kind, checked_character, checked_scope, checked_page),
    )


def _scope(value: Scope | None) -> Scope:
    if value not in {"group", "bot"}:
        raise GameWorkflowRejected("scope must be group or bot")
    return value


def _page(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 999:
        raise GameWorkflowRejected("page must be an integer from 1 to 999")
    return value


__all__ = [
    "GameWorkflowRejected",
    "NTEWorkflow",
    "NTE_WORKFLOW_ACTIONS",
    "WuwaWorkflow",
    "WUWA_WORKFLOW_ACTIONS",
    "build_nte_workflow",
    "build_wuwa_workflow",
]
