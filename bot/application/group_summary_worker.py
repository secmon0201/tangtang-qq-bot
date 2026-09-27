"""Daily, group-isolated bulk summaries for natural-language context."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from nonebot import logger

from bot.application.personas import persona_engine
from bot.services.group_summary import GroupSummaryService
from bot.services.runtime import database, group_domains
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_runtime import config_loader


DAILY_BATCH_MESSAGES = 2000


async def _summarize_group(history: TangtangDb, provider: TangtangProvider,
                           group_id: int, day: str) -> None:
    cutoff_id = await asyncio.to_thread(history.latest_group_message_id, group_id)
    if not await asyncio.to_thread(
        history.claim_group_daily_digest_run, group_id, day, cutoff_id, now=_timestamp()
    ):
        return
    engine = persona_engine()
    service = GroupSummaryService(
        history, provider, config_loader, chat_id=_timestamp,
        central=engine.store,
        enabled=lambda gid: engine.chat_enabled(gid, False) or engine.chat_enabled(gid, True),
    )
    cursor = await asyncio.to_thread(
        history.latest_group_daily_digest_cutoff, group_id
    )
    batch_index = 0
    try:
        while True:
            rows = await asyncio.to_thread(
                history.group_daily_digest_pending,
                group_id, after_id=cursor, cutoff_id=cutoff_id,
                limit=DAILY_BATCH_MESSAGES,
            )
            if not rows:
                break
            await service.apply_daily_digest(group_id, day, batch_index, rows, cutoff_id)
            cursor = int(rows[-1]['id'])
            batch_index += 1
            await asyncio.sleep(0)
        await asyncio.to_thread(
            history.finish_group_daily_digest_run, group_id, day,
            now=_timestamp(), status='completed',
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await asyncio.to_thread(
            history.finish_group_daily_digest_run, group_id, day,
            now=_timestamp(), status=type(exc).__name__,
        )
        logger.warning('Daily group summary failed for {}: {}', group_id, type(exc).__name__)


async def run_group_summaries() -> None:
    """Run at most one durable summary pass per group per local calendar day."""

    history = TangtangDb()
    history.blocked_users = database().blocked_user_ids
    provider = TangtangProvider()
    while True:
        try:
            config = config_loader.load()
            if config.enabled and config.group_summary_enabled:
                now_dt = datetime.now().astimezone()
                day = now_dt.date().isoformat()
                eligible = tuple(
                    group_id for group_id in group_domains().all_group_ids()
                    if persona_engine().chat_enabled(group_id, False)
                    or persona_engine().chat_enabled(group_id, True)
                )
                for group_id in eligible:
                    await _summarize_group(history, provider, group_id, day)
                    await asyncio.sleep(0)
                next_midnight = (now_dt + timedelta(days=1)).replace(
                    hour=0, minute=0, second=5, microsecond=0
                )
                await asyncio.sleep(max(5.0, (next_midnight - datetime.now().astimezone()).total_seconds()))
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Daily group summary worker recovered: {}', type(exc).__name__)
            await asyncio.sleep(60)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()
