"""Compose the personal-memory worker separately from source refresh jobs."""
import asyncio

from nonebot import logger

from bot.application.personas import persona_engine
from bot.services.persona_actions import PersonaActions
from bot.services.persona_observer import PersonaObserver
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
