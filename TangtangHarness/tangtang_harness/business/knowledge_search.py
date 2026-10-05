"""Shared parsing helpers for local curated knowledge sources."""

from __future__ import annotations

import json
import re

from tangtang_harness.business.knowledge_db import KnowledgeDb


def query_tokens(query: str) -> tuple[str, ...]:
    """Split a query into ASCII runs plus overlapping CJK bigrams."""

    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower())
    tokens: list[str] = []
    for part in parts:
        tokens.append(part)
        if len(part) >= 2 and "\u4e00" <= part[0] <= "\u9fff":
            tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
    return tuple(tokens)


def resolve_related(raw: object, db: KnowledgeDb) -> tuple[tuple[str, str], ...]:
    """Resolve visible cross-domain references while retaining stable fallbacks."""

    try:
        refs = json.loads(raw) if isinstance(raw, str) else []
        if not isinstance(refs, list):
            refs = []
    except (ValueError, TypeError):
        refs = []
    result: list[tuple[str, str]] = []
    for ref in refs:
        ref = str(ref).strip()
        if not ref or "/" not in ref:
            continue
        domain, entry_id = ref.split("/", 1)
        target = db.find_approved(domain, entry_id)
        if target is not None and not target["blocked"]:
            result.append((ref, str(target.get("title") or entry_id)))
        else:
            result.append((ref, entry_id))
    return tuple(result)
