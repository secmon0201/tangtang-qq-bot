from pathlib import Path

from bot.config import ROOT, settings
from tests.runtime_isolation import RUNTIME_ROOT, SOURCE_ROOT


def test_collection_time_default_runtime_paths_are_outside_live_checkout():
    from bot.application.chat_continuation import continuation_store
    from bot.application.personas import persona_engine
    from bot.application.proactive_chat import proactive_store
    from bot.services.knowledge_db import DEFAULT_DB_PATH as knowledge_path
    from bot.services.runtime import database
    from bot.services.tangtang_chat import USAGE_DIR
    from bot.services.tangtang_db import DEFAULT_DB_PATH as history_path
    from bot.services.tangtang_runtime import config_loader

    engine = persona_engine()
    paths = (
        settings.db_path, settings.report_dir, settings.avatar_cache_dir,
        database().path, knowledge_path, history_path, USAGE_DIR,
        engine.store.path, engine.speech.cache_dir,
        continuation_store().path, proactive_store().path, config_loader.path,
    )
    for path in paths:
        assert Path(path).is_relative_to(RUNTIME_ROOT), path
        assert not Path(path).is_relative_to(SOURCE_ROOT), path


def test_isolated_root_preserves_resources_configs_upstreams_and_path_operations():
    for relative in ("bot/resources", "config/upstream-lock.json", "GsUID.Core"):
        assert ROOT / relative == SOURCE_ROOT / relative
    for relative in ("data", "logs", "downloads", "backups", ".env"):
        assert ROOT / relative == RUNTIME_ROOT / relative
    assert ROOT.joinpath("data", "history", "chat.db") == RUNTIME_ROOT / "data/history/chat.db"
    assert ROOT.resolve() / "data" == RUNTIME_ROOT / "data"
    assert (ROOT / "bot/resources/personas/denia/persona.md").is_file()
