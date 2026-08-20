from __future__ import annotations

import pytest

from bot.services.knowledge_db import KnowledgeDb
from bot.services.knowledge_review import (
    export_pending_doc,
    export_rejected_doc,
    parse_review_doc,
    process_review_files,
)


def _propose(
    db: KnowledgeDb,
    *,
    domain: str = "zhijiang",
    entry_id: str,
    title: str,
) -> int:
    return db.propose_entry(
        domain=domain,
        entry_id=entry_id,
        title=title,
        summary=f"{title} 的摘要",
        tags=("测试",),
        category="",
        source_name="测试来源",
        source_url=f"https://example.com/{entry_id}",
        source_note="测试说明",
    )


def test_export_pending_doc_roundtrip(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    _propose(db, entry_id="web-one", title="测试词条一")
    doc = tmp_path / "收录待审.md"
    export_pending_doc(db, doc)
    parsed = parse_review_doc(doc)
    assert len(parsed) == 1
    assert parsed[0]["entry_id"] == "web-one"
    assert parsed[0]["title"] == "测试词条一"
    assert parsed[0]["tags"] == ["测试"]
    assert parsed[0]["source_url"] == "https://example.com/web-one"


def test_export_rejected_doc_lists_archived_rejections(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    first_id = _propose(db, entry_id="web-bad-1", title="拒绝词条一")
    second_id = _propose(db, entry_id="web-bad-2", title="拒绝词条二")
    db.reject_entry(first_id)
    db.reject_entry(second_id)
    doc = tmp_path / "收录拒绝归档.md"

    export_rejected_doc(db, doc)

    text = doc.read_text(encoding="utf-8")
    assert "# 收录拒绝归档（共 2 条）" in text
    assert "### zhijiang/web-bad-1" in text
    assert "拒绝词条二" in text


def test_process_review_files_approves_and_clears(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    _propose(db, entry_id="web-one", title="测试词条一")
    _propose(db, entry_id="web-two", title="测试词条二")
    approve_doc = tmp_path / "收录确认.md"
    approve_doc.write_text(
        "### zhijiang/web-one\n"
        "- domain: zhijiang\n"
        "- entry_id: web-one\n"
        "- title: 测试词条一\n"
        "- summary: 测试词条一 的摘要\n"
        "- tags: 测试\n"
        "- source_name: 测试来源\n"
        "- source_url: https://example.com/web-one\n",
        encoding="utf-8",
    )
    reject_doc = tmp_path / "收录拒绝.md"
    reject_doc.write_text("# 空\n", encoding="utf-8")
    pending_doc = tmp_path / "收录待审.md"

    summary = process_review_files(db, pending_doc, approve_doc, reject_doc)

    assert summary["approved"] == ["zhijiang/web-one"]
    assert summary["rejected"] == []
    approved_ids = {row["entry_id"] for row in db.approved_entries("zhijiang")}
    assert "web-one" in approved_ids
    assert "web-two" not in approved_ids
    assert {row["entry_id"] for row in db.pending_entries()} == {"web-two"}
    assert "测试词条一" not in approve_doc.read_text(encoding="utf-8")
    assert "测试词条二" in pending_doc.read_text(encoding="utf-8")


def test_process_review_files_rejects_archives_and_blocks_duplicates(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    _propose(db, entry_id="web-bad", title="不要的词条")
    approve_doc = tmp_path / "收录确认.md"
    approve_doc.write_text("# 空\n", encoding="utf-8")
    reject_doc = tmp_path / "收录拒绝.md"
    reject_doc.write_text(
        "### zhijiang/web-bad\n"
        "- domain: zhijiang\n"
        "- entry_id: web-bad\n"
        "- title: 不要的词条\n"
        "- summary: 不要的词条 的摘要\n"
        "- source_name: 测试来源\n"
        "- source_url: https://example.com/web-bad\n",
        encoding="utf-8",
    )

    summary = process_review_files(db, tmp_path / "收录待审.md", approve_doc, reject_doc)

    assert summary["rejected"] == ["zhijiang/web-bad"]
    assert db.pending_entries() == []
    assert any(row["entry_id"] == "web-bad" for row in db.rejected_entries())
    with pytest.raises(ValueError, match="previously rejected"):
        _propose(db, entry_id="web-bad-again", title="不要的词条")


def test_process_review_files_matches_pending_by_title(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    _propose(db, entry_id="web-orig", title="同名词条")
    approve_doc = tmp_path / "收录确认.md"
    approve_doc.write_text(
        "### zhijiang/web-copy\n"
        "- domain: zhijiang\n"
        "- entry_id: web-copy\n"
        "- title: 同名词条\n"
        "- summary: 同名词条 的摘要\n"
        "- source_name: 测试来源\n"
        "- source_url: https://example.com/web-copy\n",
        encoding="utf-8",
    )
    reject_doc = tmp_path / "收录拒绝.md"
    reject_doc.write_text("# 空\n", encoding="utf-8")

    process_review_files(db, tmp_path / "收录待审.md", approve_doc, reject_doc)

    assert db.pending_entries() == []
    assert any(row["entry_id"] == "web-orig" for row in db.approved_entries("zhijiang"))


def test_process_review_files_reports_forbidden_as_invalid(tmp_path):
    db = KnowledgeDb(tmp_path / "knowledge.db")
    approve_doc = tmp_path / "收录确认.md"
    approve_doc.write_text(
        "### zhijiang/web-carol\n"
        "- domain: zhijiang\n"
        "- entry_id: web-carol\n"
        "- title: 珈乐相关词条\n"
        "- summary: 摘要\n"
        "- source_name: 测试来源\n"
        "- source_url: https://example.com/carol\n",
        encoding="utf-8",
    )
    reject_doc = tmp_path / "收录拒绝.md"
    reject_doc.write_text("# 空\n", encoding="utf-8")

    summary = process_review_files(db, tmp_path / "收录待审.md", approve_doc, reject_doc)

    assert summary["approved"] == []
    assert summary["invalid"]
    assert db.pending_entries() == []
    assert not any(row["entry_id"] == "web-carol" for row in db.approved_entries("zhijiang"))
