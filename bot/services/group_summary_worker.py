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
    prepared: dict[int, GroupSummaryWorker] = {}
    seeded: set[int] = set()
    while True:
        try:
            config = config_loader.load()
            if config.enabled and config.group_summary_enabled:
                eligible = tuple(
                    group_id
                    for group_id in group_domains().all_group_ids()
                    if engine.chat_enabled(group_id, False) or engine.chat_enabled(group_id, True)
                )
                # Seed the tail for every group before the first model call so
                # later groups cannot be blocked behind an active one.
                pending_seed = tuple(group_id for group_id in eligible if group_id not in seeded)
                if pending_seed:
                    tails: dict[int, int] = {}
                    for group_id in pending_seed:
                        persona = engine.store.selection(group_id)[0]
                        group_db = engine.history(persona, history)
                        tails[group_id] = await asyncio.to_thread(
                            group_db.group_summary_seed, group_id, now=_timestamp()
                        )
                        seeded.add(group_id)
                if set(prepared) != set(eligible):
                    prepared.clear()
                    for group_id in eligible:
                        persona = engine.store.selection(group_id)[0]
                        group_db = engine.history(persona, history)
                        prepared[group_id] = GroupSummaryWorker(
                            GroupSummaryService(
                                group_db,
                                provider,
                                config_loader,
                                chat_id=lambda: _timestamp(),
                            ),
                            batch_messages=config.group_summary_batch_messages,
                        )
                # Round-robin one bounded batch per group so a busy group cannot
                # starve the first summary of every other group.
                for group_id in eligible:
                    await prepared[group_id].tick((group_id,))
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Group summary worker recovered: {}", type(exc).__name__)
        await asyncio.sleep(2)


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()
