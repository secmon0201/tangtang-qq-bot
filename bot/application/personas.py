"""Compose persona runtime with project-owned integrations and existing gates."""
from __future__ import annotations

from functools import lru_cache
import hashlib

from bot.config import ROOT, settings
from bot.integrations.sovits import SovitsBackend
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_store import PersonaStore
from bot.services.runtime import database, group_domains, passive_settings
from bot.services.speech import SpeechService
from bot.services.tangtang_runtime import config_loader
from bot.services.tangtang_db import TangtangDb


def chat_enabled(group_id: int, proactive: bool) -> bool:
    feature = "proactive_chat" if proactive else "mention_chat"
    config = config_loader.load()
    return (config.enabled and (not proactive or config.proactive_enabled)
            and passive_settings().is_chat_globally_enabled(feature)
            and group_domains().effective_feature_enabled(group_id, feature))


@lru_cache(maxsize=1)
def persona_engine() -> PersonaEngine:
    store = PersonaStore(ROOT / "data" / "personas" / "state.db", settings.timezone)
    speech = SpeechService(store, SovitsBackend(), ROOT / "data" / "personas" / "audio")
    return PersonaEngine(store, speech,
        history_db=TangtangDb(),
        feature_enabled=lambda group, feature: group_domains().effective_feature_enabled(group, feature),
        chat_enabled=chat_enabled,
        gate_revision=lambda group: database().chat_gate_revision(group),
        configuration_version=lambda: hashlib.sha256(repr(config_loader.load()).encode()).hexdigest())
