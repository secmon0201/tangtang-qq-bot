"""Tests for Agent context semantic compaction and token watermarks (Q12)."""

import json
import pytest

from bot.services.context_compaction import (
    COMPACTION_SYSTEM_PROMPT,
    compaction_prompt,
    parse_snapshot,
    validate_snapshot,
)
from bot.services.tangtang_chat import _context_compaction_due


def test_compaction_system_prompt_mandates_asset_protection_and_noise_pruning():
    assert "专属称谓" in COMPACTION_SYSTEM_PROMPT
    assert "情感羁绊" in COMPACTION_SYSTEM_PROMPT
    assert "未决" in COMPACTION_SYSTEM_PROMPT
    assert "历史工具调用" in COMPACTION_SYSTEM_PROMPT
    assert "一句话动作结论" in COMPACTION_SYSTEM_PROMPT
    assert "复读刷屏" in COMPACTION_SYSTEM_PROMPT


def test_snapshot_character_limit_enforced_at_six_thousand():
    source_ids = (101, 102)
    revision = 2
    valid_data = {
        "facts": ["用户称呼为小明", "已查询发言日榜"],
        "commitments": ["约定今晚八点讨论活动"],
        "unresolved": ["鸣潮抽卡建议待补充"],
        "topic_progress": ["第一阶段讨论完毕"],
        "source_turn_ids": [101, 102],
        "scope": "session",
        "revision": revision,
    }
    snapshot = validate_snapshot(valid_data, source_turn_ids=source_ids, revision=revision)
    assert snapshot["revision"] == revision
    assert snapshot["facts"][0] == "用户称呼为小明"

    bloated_facts = ["冗长数据块: " + "A" * 500 for _ in range(15)]
    oversized_data = dict(valid_data)
    oversized_data["facts"] = bloated_facts
    with pytest.raises(ValueError, match="exceeds 6000 characters"):
        validate_snapshot(oversized_data, source_turn_ids=source_ids, revision=revision)


def test_token_watermark_dual_trigger():
    assert not _context_compaction_due(
        30, prompt_tokens=15_000, cache_status="reported", serialized_chars=5_000
    )
    assert _context_compaction_due(
        31, prompt_tokens=10_000, cache_status="reported", serialized_chars=5_000
    )
    assert _context_compaction_due(
        35, prompt_tokens=12_500, cache_status="reported", serialized_chars=8_000
    )
    assert not _context_compaction_due(
        35, prompt_tokens=8_000, cache_status="reported", serialized_chars=18_000
    )
    assert _context_compaction_due(
        51, prompt_tokens=1_000, cache_status="reported", serialized_chars=2_000
    )
    assert _context_compaction_due(
        32, prompt_tokens=2_000, cache_status="unsupported", serialized_chars=12_000
    )
