from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tangtang_harness.business.config import ROOT, RESOURCE_DIR
from tangtang_harness.business.knowledge_db import KnowledgeDb


PENDING_DOC_PATH = ROOT / "runtime" / "knowledge" / "收录待审.md"
APPROVE_DOC_PATH = ROOT / "runtime" / "knowledge" / "收录确认.md"
REJECT_DOC_PATH = ROOT / "runtime" / "knowledge" / "收录拒绝.md"
REJECTED_DOC_PATH = ROOT / "runtime" / "knowledge" / "收录拒绝归档.md"

_FIELD_NAMES = (
    "domain",
    "entry_id",
    "title",
    "summary",
    "tags",
    "category",
    "source_name",
    "source_url",
    "source_note",
    "conflict_note",
)

_APPROVE_TEMPLATE = """# 收录确认

把「收录待审.md」里可以通过的条目整块复制到本文件；
机器人每次启动会处理本文件并清空。禁止添加违禁内容。
"""

_REJECT_TEMPLATE = """# 收录拒绝

把「收录待审.md」里不能通过的条目整块复制到本文件；
机器人每次启动会归档拒绝并清空本文件，后续重复内容将自动拒绝。
"""


def _entry_block(item: dict[str, Any]) -> str:
    lines = [f"### {item.get('domain')}/{item.get('entry_id')}"]
    for name in _FIELD_NAMES:
        value = item.get(name, "")
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    value = parsed
            except (ValueError, TypeError):
                pass
        if isinstance(value, (list, tuple)):
            value = "、".join(str(part) for part in value)
        lines.append(f"- {name}: {value}")
    return "\n".join(lines)


def export_pending_doc(
    db: KnowledgeDb | None = None,
    path: Path | None = None,
) -> Path:
    """Write the pending queue into a readable markdown document."""

    db = db or KnowledgeDb()
    path = path or PENDING_DOC_PATH
    rows = db.pending_entries()
    blocks = [_entry_block(row) for row in rows]
    header = f"# 收录待审（共 {len(rows)} 条）\n\n"
    if blocks:
        header += (
            "通过的条目整块复制到「收录确认.md」，不通过的复制到「收录拒绝.md」；"
            "机器人每次启动会处理并清空这两份文档。\n\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n\n".join(blocks), encoding="utf-8")
    return path


def export_rejected_doc(
    db: KnowledgeDb | None = None,
    path: Path | None = None,
) -> Path:
    """Write the permanent rejected archive (statistics + duplicate rejection)."""

    db = db or KnowledgeDb()
    path = path or REJECTED_DOC_PATH
    rows = db.rejected_entries()
    blocks = [_entry_block(row) for row in rows]
    header = (
        f"# 收录拒绝归档（共 {len(rows)} 条）\n\n"
        "以下内容已拒绝收录，仅用于统计和后续重复内容自动拒绝；"
        "不会进入糖糖检索。\n\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n\n".join(blocks), encoding="utf-8")
    return path


def parse_review_doc(path: Path) -> list[dict[str, Any]]:
    """Parse blocks copied from the pending document (### domain/entry_id + key lines)."""

    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    for block in re.split(r"(?m)^### ", text)[1:]:
        entry: dict[str, Any] = {}
        for raw_line in block.splitlines():
            if not raw_line.startswith("- "):
                continue
            body = raw_line[2:]
            if ":" not in body:
                continue
            key, value = body.split(":", 1)
            entry[key.strip()] = value.strip()
        if not entry.get("domain") or not entry.get("entry_id"):
            continue
        tags = entry.get("tags", "")
        entry["tags"] = [tag.strip() for tag in tags.split("、") if tag.strip()] if tags else []
        entries.append(entry)
    return entries


def process_review_files(
    db: KnowledgeDb | None = None,
    pending_path: Path | None = None,
    approve_path: Path | None = None,
    reject_path: Path | None = None,
) -> dict[str, Any]:
    """Process 收录确认.md / 收录拒绝.md, clear them, refresh the pending doc.

    Entries matched against the pending queue are approved or archived as
    rejected; unmatched entries are inserted directly and then approved or
    rejected. Rejected entries stay in the database so later duplicates are
    automatically refused.
    """

    db = db or KnowledgeDb()
    pending_path = pending_path or PENDING_DOC_PATH
    approve_path = approve_path or APPROVE_DOC_PATH
    reject_path = reject_path or REJECT_DOC_PATH
    summary: dict[str, Any] = {"approved": [], "rejected": [], "invalid": []}

    for doc_path, action in ((approve_path, "approve"), (reject_path, "reject")):
        for item in parse_review_doc(doc_path):
            domain = str(item.get("domain") or "")
            entry_id = str(item.get("entry_id") or "")
            title = str(item.get("title") or "")
            target = summary["approved"] if action == "approve" else summary["rejected"]
            try:
                pending = db.find_pending(domain, entry_id)
                if pending is None:
                    pending = db.find_pending_by_title(domain, title)
                if pending is not None:
                    if action == "approve":
                        db.approve_entry(pending["id"])
                    else:
                        db.reject_entry(pending["id"])
                else:
                    row_id = db.propose_entry(
                        domain=domain,
                        entry_id=entry_id,
                        title=title,
                        summary=str(item.get("summary") or ""),
                        tags=item.get("tags", []),
                        category=str(item.get("category") or ""),
                        source_name=str(item.get("source_name") or ""),
                        source_url=str(item.get("source_url") or ""),
                        source_note=str(item.get("source_note") or ""),
                        conflict_note=str(item.get("conflict_note") or ""),
                    )
                    if action == "approve":
                        db.approve_entry(row_id)
                    else:
                        db.reject_entry(row_id)
                target.append(f"{domain}/{entry_id}")
            except ValueError as exc:
                message = str(exc)
                if action == "reject" and "previously rejected" in message:
                    target.append(f"{domain}/{entry_id} (already rejected)")
                elif "previously rejected" in message:
                    summary["invalid"].append(f"{domain}/{entry_id}: {message}")
                elif "already exists" in message or "same title already exists" in message:
                    target.append(f"{domain}/{entry_id} (already present)")
                else:
                    summary["invalid"].append(f"{domain}/{entry_id}: {message}")

    approve_path.parent.mkdir(parents=True, exist_ok=True)
    reject_path.parent.mkdir(parents=True, exist_ok=True)
    approve_path.write_text(_APPROVE_TEMPLATE, encoding="utf-8")
    reject_path.write_text(_REJECT_TEMPLATE, encoding="utf-8")
    export_pending_doc(db, pending_path)
    return summary
