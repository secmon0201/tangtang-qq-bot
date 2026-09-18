"""Compose the personal-memory worker separately from source refresh jobs."""
import asyncio

from nonebot import logger

from bot.application.personas import persona_engine
from bot.services.persona_actions import PersonaActions
from bot.services.persona_observer import PersonaObserver
from bot.services.persona_profile_history import rebuild_from_history
from bot.services.persona_profile_worker import ProfileWorker
from bot.services.runtime import group_domains
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_runtime import config_loader


async def run_personal_memory():
    engine = persona_engine()
    worker = PersonaObserver(engine, TangtangDb(), TangtangProvider(), config_loader)
    if engine.v2_enabled('denia'):
        await asyncio.to_thread(PersonaActions(worker.cognition).recover)
        await asyncio.to_thread(worker.inbox.recover)
    while True:
        try:
            active = {g for g in group_domains().all_group_ids()
                      if engine.chat_enabled(g, False) or engine.chat_enabled(g, True)}
            await worker.tick(active)
        except Exception as exc:
            logger.warning('Personal memory worker recovered: {}', type(exc).__name__)
        await asyncio.sleep(2)


async def run_personal_profiles():
    engine = persona_engine()
    db = TangtangDb()
    cognition = engine.cognition('denia', db)
    worker = ProfileWorker(cognition, engine.store, TangtangProvider(), config_loader)
    await asyncio.to_thread(worker.profiles.recover)
    while True:
        try:
            if engine.v2_enabled('denia'):
                await asyncio.to_thread(rebuild_from_history, cognition, db, engine.store)
                await asyncio.to_thread(mirror_profile_sources, cognition, db)
                await worker.tick()
        except Exception as exc:
            logger.warning('Personal profile worker recovered: {}', type(exc).__name__)
        await asyncio.sleep(2)


def mirror_profile_sources(cognition, db):
    # Inbox entries were admitted by the existing receive-time observation gates.
    # This cursor only mirrors evidence; it never consumes the chat extractor's inbox.
    with cognition.connect() as conn:
        row = conn.execute("SELECT completed_at FROM persona_profile_migrations WHERE version='inbox_cursor'").fetchone()
        cursor = int(row[0]) if row else 0
    with db._connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM persona_observation_inbox WHERE persona='denia' AND id>? ORDER BY id LIMIT 100", (cursor,))]
    if rows:
        cognition.import_sources(rows)
        with cognition.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO persona_profile_migrations VALUES('inbox_cursor',?)", (rows[-1]['id'],))
