from __future__ import annotations

import json
from dataclasses import dataclass

from bot.services.knowledge_db import KnowledgeDb
from bot.services.knowledge_search import query_tokens, resolve_related


_db = KnowledgeDb()


@dataclass(frozen=True, slots=True)
class MingchaoMemeEntry:
    entry_id: str
    title: str
    summary: str
    tags: tuple[str, ...]
    category: str
    source_name: str
    source_url: str
    source_note: str
    blocked: bool = False
    updated_at: str = ""
    status: str = "approved"
    source_priority: int = 0
    conflict_note: str = ""
    related: tuple[tuple[str, str], ...] = ()


def _entry_from_row(row: dict, db: KnowledgeDb) -> MingchaoMemeEntry:
    try:
        raw_tags = row.get("tags") or "[]"
        tag_list = json.loads(raw_tags) if isinstance(raw_tags, str) else []
        if not isinstance(tag_list, list):
            tag_list = []
    except (ValueError, TypeError):
        tag_list = []
    related = resolve_related(row.get("related_ids") or "[]", db)
    return MingchaoMemeEntry(
        entry_id=str(row.get("entry_id") or ""),
        title=str(row.get("title") or ""),
        summary=str(row.get("summary") or ""),
        tags=tuple(str(tag) for tag in tag_list),
        category=str(row.get("category") or ""),
        source_name=str(row.get("source_name") or ""),
        source_url=str(row.get("source_url") or ""),
        source_note=str(row.get("source_note") or ""),
        blocked=bool(row.get("blocked")),
        updated_at=str(row.get("updated_at") or ""),
        status=str(row.get("status") or "approved"),
        source_priority=int(row.get("source_priority") or 0),
        conflict_note=str(row.get("conflict_note") or ""),
        related=related,
    )


def entries() -> tuple[MingchaoMemeEntry, ...]:
    return tuple(_entry_from_row(row, _db) for row in _db.approved_entries("mingchao"))


def search(query: str, limit: int = 3) -> tuple[MingchaoMemeEntry, ...]:
    tokens = query_tokens(query)
    visible = tuple(entry for entry in entries() if not entry.blocked)
    if not tokens:
        return visible[:limit]
    scored: list[tuple[int, MingchaoMemeEntry]] = []
    for entry in visible:
        title = entry.title.lower()
        tags = " ".join(entry.tags).lower()
        summary = entry.summary.lower()
        score = sum(
            5 if token in title else 3 if token in tags else 1 if token in summary else 0
            for token in tokens
        )
        if score:
            scored.append((score, entry))
    return tuple(
        entry
        for _score, entry in sorted(scored, key=lambda item: (-item[0], item[1].entry_id))[:limit]
    )


def render_search(query: str, limit: int = 3) -> str:
    matches = search(query, limit)
    if not matches:
        return "本地鸣潮梗文化库未找到相关条目。可尝试：鸣潮公式、牢卡、雪豹、小土豆、潮友、乃琳、嘉然、贝拉、库洛、战双。"
    blocks = []
    for entry in matches:
        note = f"\n来源说明：{entry.source_note}" if entry.source_note else ""
        related = ""
        if entry.related:
            related = "\n相关词条：" + "、".join(title for _ref, title in entry.related)
        blocks.append(
            f"《{entry.title}》\n{entry.summary}\n来源：{entry.source_name} "
            f"{entry.source_url}{note}{related}"
        )
    return "\n\n".join(blocks)
