from __future__ import annotations

import os

import pytest
from tests.runtime_isolation import install_runtime_isolation

# Tests must never inherit identities or public endpoints from a developer's
# private .env. These values are deliberately synthetic and documentation-safe.
os.environ["GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER"] = "测试集群"
os.environ["WUWA_IMPORT_CLUSTER_NAME"] = "测试集群"
os.environ["PUBLIC_SITE_BASE_URL"] = "https://bot.example.invalid"
os.environ["PUBLIC_SHORT_HOST"] = "short.example.invalid"
os.environ["PUBLIC_GENERATOR_CREDIT"] = "Generated locally"

# Import-time plugin composition precedes fixtures, so isolate it first.
install_runtime_isolation()

from bot.services import mingchao_meme_culture, zhijiang_knowledge
from bot.services.knowledge_db import KnowledgeDb


@pytest.fixture(autouse=True)
def isolated_knowledge_db(tmp_path, monkeypatch):
    """Point both local knowledge services at a temp SQLite store per test."""

    db = KnowledgeDb(tmp_path / "knowledge.db")
    monkeypatch.setattr(zhijiang_knowledge, "_db", db)
    monkeypatch.setattr(mingchao_meme_culture, "_db", db)
    return db
