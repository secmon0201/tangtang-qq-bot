from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "snowluma-onebot-contract.json").read_text(encoding="utf-8"))


def _literal_onebot_actions() -> set[str]:
    argument_index = {
        "_request": 0,
        "call_qq_action": 1,
        "paced_call_api": 1,
    }
    actions: set[str] = set()
    for path in (ROOT / "bot").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            index = argument_index.get(name)
            if index is None or len(node.args) <= index:
                continue
            value = node.args[index]
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                actions.add(value.value)
    return actions


def test_every_literal_onebot_action_is_covered_by_the_pinned_snowluma_contract():
    required = set(CONTRACT["required_actions"])
    used = _literal_onebot_actions()

    assert used <= required, f"Unreviewed OneBot actions: {sorted(used - required)}"
    assert required == {
        "delete_msg",
        "get_cookies",
        "get_csrf_token",
        "get_group_info",
        "get_group_list",
        "get_group_member_info",
        "get_group_member_list",
        "get_group_msg_history",
        "get_login_info",
        "send_group_forward_msg",
        "send_group_msg",
        "send_private_forward_msg",
        "send_private_msg",
        "set_group_ban",
        "set_msg_emoji_like",
    }


def test_contract_and_release_lock_refer_to_the_same_reviewed_source():
    lock = json.loads((ROOT / "config" / "snowluma-lock.json").read_text(encoding="utf-8"))

    assert CONTRACT["version"] == lock["version"]
    assert CONTRACT["source_commit"] == lock["source_commit"]
    assert CONTRACT["response_adapters"]["get_cookies"].endswith("get_csrf_token fallback")
    assert CONTRACT["response_adapters"]["get_group_msg_history"].endswith(
        "reverse_order=true"
    )
