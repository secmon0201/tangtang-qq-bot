"""Owned background loop for incremental group summaries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

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
    # Group topics read the shared raw group-message corpus, not the sparse
    # persona interaction log.
    history = TangtangDb()
    prepared: dict[int, GroupSummaryWorker] = {}
    seeded: set[int] = set()
    while True:
        try:
            config = config_loader.load()
            if config.enabled and config.group_summary_enabled:
                cutoff = (
                    datetime.now().astimezone()
                    - timedelta(hours=config.group_summary_max_age_hours)
                ).isoformat()
                eligible = tuple(
                    group_id
                    for group_id in group_domains().all_group_ids()
                    if engine.chat_enabled(group_id, False) or engine.chat_enabled(group_id, True)
                )
                # Seed the tail for every group before the first model call so
                # later groups cannot be blocked behind an active one.
                pending_seed = tuple(group_id for group_id in eligible if group_id not in seeded)
                if pending_seed:
                    for group_id in pending_seed:
                        await asyncio.to_thread(
                            history.group_summary_seed,
                            group_id,
                            now=_timestamp(),
                            source="raw",
                        )
                        seeded.add(group_id)
                if set(prepared) != set(eligible):
                    prepared.clear()
                    for group_id in eligible:
                        prepared[group_id] = GroupSummaryWorker(
                            GroupSummaryService(
                                history,
                                provider,
                                config_loader,
                                chat_id=lambda: _timestamp(),
                            ),
                            batch_messages=config.group_summary_batch_messages,
                        )
                # Round-robin one bounded batch per group so a busy group cannot
                # starve the first summary of every other group.
                for group_id in eligible:
                    await asyncio.to_thread(
                        prepared[group_id].service.db.group_summary_skip_older_than,
                        group_id,
                        cutoff,
                    )
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
