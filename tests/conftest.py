from __future__ import annotations

import pytest

from bot.services import mingchao_meme_culture, zhijiang_knowledge
from bot.services.knowledge_db import KnowledgeDb


@pytest.fixture(autouse=True)
def isolated_knowledge_db(tmp_path, monkeypatch):
    """Point both local knowledge services at a temp SQLite store per test."""

    db = KnowledgeDb(tmp_path / "knowledge.db")
    monkeypatch.setattr(zhijiang_knowledge, "_db", db)
    monkeypatch.setattr(mingchao_meme_culture, "_db", db)
    return db
