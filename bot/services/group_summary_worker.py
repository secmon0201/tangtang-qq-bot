"""Owned background loop for incremental group summaries."""
from __future__ import annotations

import asyncio

from nonebot import logger

from bot.application.personas import persona_engine
from bot.services.group_summary import GroupSummaryService, GroupSummaryWorker
from bot.services.runtime import group_domains
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_runtime import config_loader


async def run_group_summaries() -> None:
    """Keep one bounded summarize pass per enabled group, independent of chat."""

    engine = persona_engine()
    provider = TangtangProvider()
    history = TangtangDb()
    while True:
        try:
            config = config_loader.load()
            if config.enabled and config.group_summary_enabled:
                for group_id in group_domains().all_group_ids():
                    if not (engine.chat_enabled(group_id, False) or engine.chat_enabled(group_id, True)):
                        continue
                    persona = engine.store.selection(group_id)[0]
                    group_db = engine.history(persona, history)
                    await asyncio.to_thread(group_db.group_summary_seed, group_id, now=_timestamp())
                    worker = GroupSummaryWorker(
                        GroupSummaryService(
                            group_db,
                            provider,
                            config_loader,
                            chat_id=lambda: _timestamp(),
                        ),
                        batch_messages=config.group_summary_batch_messages,
                    )
                    await worker.tick((group_id,))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Group summary worker recovered: {}", type(exc).__name__)
        await asyncio.sleep(5)


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()
