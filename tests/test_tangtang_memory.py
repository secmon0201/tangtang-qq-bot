from __future__ import annotations

from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_memory import TangtangMemoryKernel


def _kernel(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    return db, TangtangMemoryKernel(db, lambda: "2026-09-06T12:00:00+08:00")


def test_explicit_memory_is_group_local_and_not_other_users(tmp_path):
    _db, kernel = _kernel(tmp_path)
    kernel.observe_user_message(
        group_id=1, user_id=2, message_id="m1", text="糖糖记住我喜欢草莓"
    )
    assert "喜欢草莓" in kernel.recall(1, 2, "草莓").prompt_text()
    assert kernel.recall(1, 3, "草莓").rows == ()
    assert "喜欢草莓" not in kernel.recall(9, 2, "草莓").prompt_text()


def test_stable_preference_needs_repeated_evidence(tmp_path):
    _db, kernel = _kernel(tmp_path)
    for message_id in ("m1", "m2"):
        kernel.observe_user_message(
            group_id=1,
            user_id=2,
            message_id=message_id,
            text="我喜欢看嘉然直播",
        )
        if message_id == "m1":
            assert kernel.recall(1, 2, "嘉然直播").rows == ()
    assert "喜欢看嘉然直播" in kernel.recall(1, 2, "嘉然直播").prompt_text()


def test_sensitive_content_is_not_memorised_and_forget_is_reversible_state(tmp_path):
    db, kernel = _kernel(tmp_path)
    assert kernel.observe_user_message(
        group_id=1, user_id=2, message_id="m1", text="记住我的密码是123456"
    ) is None
    kernel.observe_user_message(
        group_id=1, user_id=2, message_id="m2", text="记住我喜欢草莓蛋糕"
    )
    assert kernel.apply_forget_request(1, 2, "糖糖忘记草莓蛋糕") == 1
    assert kernel.recall(1, 2, "草莓蛋糕").rows == ()
    rows = db.active_memories(1, 2)
    assert rows == []
    assert kernel.apply_restore_request(1, 2, "糖糖恢复记忆草莓蛋糕") == 1
    assert "草莓蛋糕" in kernel.recall(1, 2, "草莓蛋糕").prompt_text()


def test_self_versions_and_persona_states_are_bounded(tmp_path):
    db, kernel = _kernel(tmp_path)
    kernel.ensure_self_version("糖糖 SELF v1")
    kernel.ensure_self_version("糖糖 SELF v2")
    kernel.update_states_after_reply(1, 2, "谢谢糖糖，哈哈")
    relationship = db.relationship_state(1, 2)
    persona = db.persona_state(1)
    assert relationship["interaction_count"] == 1
    assert 0.0 <= relationship["warmth"] <= 1.0
    assert 0.0 <= persona["valence"] <= 1.0
