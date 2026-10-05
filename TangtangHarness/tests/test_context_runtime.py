# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
from dataclasses import replace

import pytest

from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import ContextBudgetError, build_context, payload_diff
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def event(mid="1", *, user=101, group=201, text="你好", nickname="合成群友"):
    return InboundEvent(mid, 999, user, group, text, sender={"nickname": nickname}, timestamp=100)


def configuration(tmp_path, **kwargs):
    model = ModelProfile("synthetic", "Synthetic", "custom", "model", "https://example.invalid/v1")
    kwargs['extra'] = {**kwargs.get('extra', {}), 'context_mode': 'legacy'}
    return HarnessConfig(root=tmp_path, profiles=(model,), active_model=model.id, **kwargs)


def test_different_models_share_business_history_and_preserve_speaker(tmp_path):
    config, store = configuration(tmp_path), Store(tmp_path)
    first = event()
    store.append_event(first)
    built = build_context(config, store, first, config.profile())
    store.confirm_turn(first, ["收到"], "request", ["123"], built.user_content)
    second = build_context(config, store, event("2", nickname="后来昵称"), replace(config.profile(), id="another", model="another"))
    assert "合成群友" in second.messages[1]["content"]
    assert "后来昵称" not in second.messages[1]["content"]
    assert second.messages[2] == {"role": "assistant", "content": "收到"}


def test_private_tail_excludes_other_speakers_and_group_summaries(tmp_path):
    config, store = configuration(tmp_path), Store(tmp_path)
    store.append_event(event("one", text="我的爱好"))
    store.append_event(event("other", user=102, text="别人的秘密"))
    store.set_setting("group_state:group:201", {"summary": "群里别人说的话"})
    built = build_context(config, store, event("private", group=None), config.profile())
    tail = built.user_content
    assert "我的爱好" in tail
    assert "别人的秘密" not in tail
    assert "群里别人说的话" not in tail


def test_filtered_and_pure_tool_inputs_are_excluded(tmp_path):
    config, store = configuration(tmp_path), Store(tmp_path)
    blocked = event("blocked", text="不应回灌")
    store.append_event(blocked)
    store.set_setting("event_scope:" + blocked.key, {"chat_allowed": False, "kind": "tool"})
    built = build_context(config, store, event("2"), config.profile())
    assert "不应回灌" not in built.user_content
    assert built.telemetry["excluded_event_keys"] == [blocked.key]


def test_snapshot_switch_keeps_original_turns_and_old_revision(tmp_path):
    config, store = configuration(tmp_path), Store(tmp_path)
    store.confirm_turn(event("1"), ["第一轮"], "r1", ["100"], "冻结第一轮")
    first_turn = store.history("group:201")[0]
    first = store.publish_snapshot("group:201", {"facts": ["旧快照"]}, first_turn["id"])
    frozen = build_context(config, store, event("2"), config.profile())
    store.confirm_turn(event("2"), ["第二轮"], "r2", ["101"], "冻结第二轮")
    second_turn = store.history("group:201")[-1]
    store.publish_snapshot("group:201", {"facts": ["新快照"]}, second_turn["id"])
    assert frozen.snapshot_revision == first["revision"]
    assert "旧快照" in frozen.messages[1]["content"]
    assert len(store.history("group:201")) == 2
    assert len(store.snapshots("group:201")) == 2


def test_budget_trims_complete_turns_without_deleting_history(tmp_path):
    config, store = configuration(tmp_path, input_budget_tokens=1500), Store(tmp_path)
    for n in range(10):
        store.confirm_turn(event(str(n)), ["回复" * 50], "r" + str(n), [str(n + 1)], "输入" * 100)
    built = build_context(config, store, event("next"), config.profile())
    assert built.telemetry["trimmed_turn_ids"]
    assert len(store.history("group:201")) == 10
    # Every retained historical user still has its assistant in the same round.
    assert [item["role"] for item in built.messages[1:-1]] == ["user", "assistant"] * ((len(built.messages) - 2) // 2)
    with pytest.raises(ContextBudgetError):
        build_context(config, store, event("big", text="很长" * 3000), config.profile())


def test_diff_is_local_not_a_cache_hit_claim(tmp_path):
    config, store = configuration(tmp_path), Store(tmp_path)
    built = build_context(config, store, event(), config.profile())
    diff = payload_diff({"id": "old", "payload": built.payload, "telemetry": {"layers": built.layers}}, built.payload, built.layers)
    assert diff["common_prefix_bytes"] > 0
    assert diff["changed_layers"] == []
    assert "供应商" in diff["note"]
