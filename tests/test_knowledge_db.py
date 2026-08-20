from __future__ import annotations

import json

import pytest

from bot.services import mingchao_meme_culture, zhijiang_knowledge
from bot.services.knowledge_db import KnowledgeDb


def test_seed_loads_both_domains_with_metadata(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    zhijiang = db.approved_entries("zhijiang")
    mingchao = db.approved_entries("mingchao")
    assert len(zhijiang) >= 30
    assert len(mingchao) >= 30
    assert all(row["status"] == "approved" for row in zhijiang + mingchao)
    assert all(row["source_url"].startswith("https://") for row in zhijiang + mingchao)
    assert all(row["updated_at"] for row in zhijiang + mingchao)
    carol = [row for row in zhijiang if row["entry_id"] == "carol-profile"]
    assert carol and carol[0]["blocked"] == 1
    assert all(row["blocked"] == 0 for row in mingchao)
    timeline = next(row for row in zhijiang if row["entry_id"] == "wuwaves-livestreams")
    assert "mingchao/zj-nailin-wuwaves" in json.loads(timeline["related_ids"])
    nailin = next(row for row in mingchao if row["entry_id"] == "zj-nailin-wuwaves")
    assert "zhijiang/wuwaves-livestreams" in json.loads(nailin["related_ids"])


def test_services_read_from_sqlite_and_keep_search(tmp_path, monkeypatch):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    monkeypatch.setattr(zhijiang_knowledge, "_db", db)
    monkeypatch.setattr(mingchao_meme_culture, "_db", db)
    assert zhijiang_knowledge.search("嘉然是谁")[0].entry_id == "diana-profile"
    assert mingchao_meme_culture.search("鸣潮公式")[0].entry_id == "wuwa-formula"
    assert zhijiang_knowledge.search("珈乐") == ()
    assert all(not entry.blocked for entry in zhijiang_knowledge.search("A-SOUL", limit=20))


def test_propose_validates_fields_domain_and_source_url(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    with pytest.raises(ValueError, match="required"):
        db.propose_entry(
            domain="zhijiang",
            entry_id="web-x",
            title="",
            summary="内容",
            source_name="来源",
            source_url="https://example.com/x",
        )
    with pytest.raises(ValueError, match="http"):
        db.propose_entry(
            domain="zhijiang",
            entry_id="web-x",
            title="标题",
            summary="内容",
            source_name="来源",
            source_url="ftp://example.com/x",
        )
    with pytest.raises(ValueError, match="unknown"):
        db.propose_entry(
            domain="unknown",
            entry_id="web-x",
            title="标题",
            summary="内容",
            source_name="来源",
            source_url="https://example.com/x",
        )
    assert db.pending_entries() == []


def test_propose_rejects_forbidden_content(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    with pytest.raises(ValueError, match="forbidden"):
        db.propose_entry(
            domain="zhijiang",
            entry_id="web-carol",
            title="珈乐相关新条目",
            summary="来自联网检索的测试内容",
            source_name="测试来源",
            source_url="https://example.com/carol",
        )
    assert db.pending_entries() == []


def test_propose_pending_approve_reject_lifecycle(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    row_id = db.propose_entry(
        domain="zhijiang",
        entry_id="web-test",
        title="测试词条",
        summary="这是一条待审核的测试词条。",
        tags=("测试",),
        source_name="测试来源",
        source_url="https://example.com/test",
        conflict_note="可能与现有词条冲突",
    )
    pending = db.pending_entries()
    assert len(pending) == 1 and pending[0]["id"] == row_id
    assert pending[0]["status"] == "pending"
    assert pending[0]["source_priority"] == 10
    assert all(row["entry_id"] != "web-test" for row in db.approved_entries("zhijiang"))

    db.approve_entry(row_id)
    approved = db.approved_entries("zhijiang")
    row = next(row for row in approved if row["entry_id"] == "web-test")
    assert row["status"] == "approved"
    assert row["source_priority"] == 1
    assert row["conflict_note"] == "可能与现有词条冲突"
    with pytest.raises(ValueError, match="not found"):
        db.approve_entry(row_id)

    second_id = db.propose_entry(
        domain="mingchao",
        entry_id="web-test-2",
        title="待拒绝词条",
        summary="这条会被拒绝。",
        source_name="测试来源",
        source_url="https://example.com/reject",
    )
    db.reject_entry(second_id)
    assert all(row["id"] != second_id for row in db.pending_entries())
    assert any(row["id"] == second_id for row in db.rejected_entries())
    with pytest.raises(ValueError, match="not found"):
        db.reject_entry(second_id)
    with pytest.raises(ValueError, match="previously rejected"):
        db.propose_entry(
            domain="mingchao",
            entry_id="web-test-3",
            title="待拒绝词条",
            summary="重复提案",
            source_name="测试来源",
            source_url="https://example.com/dup-title",
        )


def test_propose_rejects_duplicate_entry_id(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    with pytest.raises(ValueError, match="already exists"):
        db.propose_entry(
            domain="zhijiang",
            entry_id="diana-profile",
            title="重复词条",
            summary="不允许覆盖已有词条",
            source_name="测试来源",
            source_url="https://example.com/dup",
        )


def test_propose_rejects_duplicate_title_with_different_entry_id(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    db.propose_entry(
        domain="zhijiang",
        entry_id="web-first",
        title="完全相同的标题",
        summary="先来一条",
        source_name="测试来源",
        source_url="https://example.com/first",
    )
    with pytest.raises(ValueError, match="same title"):
        db.propose_entry(
            domain="zhijiang",
            entry_id="web-second",
            title="完全相同的标题",
            summary="换个 entry_id 但标题重复",
            source_name="测试来源",
            source_url="https://example.com/second",
        )


def test_revive_entry_returns_rejected_proposal_to_pending(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    row_id = db.propose_entry(
        domain="zhijiang",
        entry_id="web-revive",
        title="待恢复词条",
        summary="摘要",
        source_name="来源",
        source_url="https://example.com/revive",
    )
    db.reject_entry(row_id)
    assert any(row["id"] == row_id for row in db.rejected_entries())
    assert db.find_pending("zhijiang", "web-revive") is None

    revived = db.revive_entry(row_id)
    assert revived["status"] == "pending"
    assert revived["source_priority"] == 10
    assert db.find_pending("zhijiang", "web-revive") is not None
    assert not any(row["id"] == row_id for row in db.rejected_entries())
    with pytest.raises(ValueError, match="not found"):
        db.revive_entry(row_id)


def test_curated_rows_resync_from_json_but_approved_rows_are_untouched(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    db.approved_entries("zhijiang")

    with db._connect() as conn:
        conn.execute(
            "UPDATE knowledge_entries SET title = '旧标题' "
            "WHERE domain = 'zhijiang' AND entry_id = 'diana-profile'"
        )
        conn.execute(
            "UPDATE knowledge_entries SET source_priority = 1, title = '人工改过的标题' "
            "WHERE domain = 'zhijiang' AND entry_id = 'bella-profile'"
        )
    db._cache.clear()

    rows = {row["entry_id"]: row for row in db.approved_entries("zhijiang")}
    assert rows["diana-profile"]["title"].startswith("嘉然")
    assert rows["bella-profile"]["title"] == "人工改过的标题"


def test_cross_references_resolve_titles_from_both_domains(tmp_path, monkeypatch):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    monkeypatch.setattr(zhijiang_knowledge, "_db", db)
    monkeypatch.setattr(mingchao_meme_culture, "_db", db)
    entry = next(
        entry
        for entry in zhijiang_knowledge.entries()
        if entry.entry_id == "wuwaves-livestreams"
    )
    titles = [title for _ref, title in entry.related]
    assert "乃琳直播《鸣潮》与企划回暖" in titles
    meme = next(
        entry
        for entry in mingchao_meme_culture.entries()
        if entry.entry_id == "zj-nailin-wuwaves"
    )
    assert "成员直播《鸣潮》时间线" in [title for _ref, title in meme.related]
