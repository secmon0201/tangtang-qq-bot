"""Background-only composition of sources, model jobs and local speech health."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import httpx
from nonebot import logger

from bot.application.personas import persona_engine
from bot.integrations.speech_runtime import SpeechRuntime
from bot.config import ROOT
from bot.services.asoul import ASoulService, CALENDAR_URL
from bot.services.persona_background import PersonaBackground
from bot.services.persona_topics import PersonaTopics
from bot.services.runtime import database, group_domains
from bot.services.speech import load_voice_profiles
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_runtime import config_loader


WW_MENU = "https://media-cdn-mingchao.kurogame.com/akiwebsite/website2.0/json/G152/zh/MainMenu.json"


async def collect_topics(topics: PersonaTopics) -> None:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(WW_MENU)
            response.raise_for_status()
            if len(response.content) > 4 * 1024 * 1024:
                raise ValueError("oversized official news index")
            items = response.json().get("article", [])
            # The official index includes bounded public article HTML. Prefer
            # it over an often-empty description; PersonaTopics strips markup.
        topics.ingest("鸣潮官网", [{"url": f"https://mc.kurogames.com/main/news/detail/{item['articleId']}",
            "title": item.get("articleTitle", ""), "body": item.get("articleContent") or item.get("articleDesc") or item.get("articleTitle", ""),
            "published_at": item.get("startTime", "")} for item in items[:20] if str(item.get("articleId", "")).isdigit()], time.time())
    except Exception as exc:
        logger.warning("Persona official-news refresh failed: {}", type(exc).__name__)
    try:
        service = ASoulService(database())
        today = datetime.now(service.timezone).date()
        days = await asyncio.wait_for(service.schedule_for_days(today, today + timedelta(days=7)), 18)
        topics.ingest("枝江日历", [{"url": CALENDAR_URL + "#" + item.key, "title": item.content,
            "body": service.render_schedule(day, "枝江日程", [item]), "published_at": item.starts_at.isoformat()}
            for day, items in days.items() for item in items], time.time())
    except Exception as exc:
        logger.warning("Persona calendar refresh failed: {}", type(exc).__name__)


async def start_background() -> None:
    engine = persona_engine()
    engine.topics = PersonaTopics(engine.store)
    worker = PersonaBackground(engine, TangtangProvider(), config_loader)
    runtime = SpeechRuntime(ROOT)
    profile_path = ROOT / "data" / "personas" / "voices.json"
    loaded_signature = None
    last_refresh = 0.0
    while True:
        try:
            signature = profile_path.stat().st_mtime_ns if profile_path.exists() else 0
            if signature != loaded_signature:
                loaded_signature = signature
                # Invalidate before reading: a broken replacement must never
                # leave the previous voice silently active.
                engine.speech.ready.clear()
                engine.speech.profiles.clear()
                engine.speech.bindings.clear()
                engine.speech.configuration_fault = True
                engine.store.set_option("voice_profiles_revision", signature)
                profiles, bindings = await asyncio.to_thread(load_voice_profiles, profile_path)
                engine.speech.profiles, engine.speech.bindings = profiles, bindings
                engine.speech.configuration_fault = False
            if engine.store.option("speech_enabled", True) and engine.speech.profiles and not engine.speech.ready:
                await runtime.ensure_started()
            await engine.speech.health_check()
        except Exception as exc:
            logger.warning("Persona speech background recovered: {}", type(exc).__name__)
        try:
            active = {g for g in group_domains().all_group_ids() if engine.chat_enabled(g, False) or engine.chat_enabled(g, True)}
            if active and engine.store.option("background_enabled", True):
                budget = engine.store.budget_used("background", "global", time.time())
                if budget < engine.store.option("background_global_limit", 12):
                    if any(engine.feature_enabled(g, "persona_topics") for g in active) and (time.time() - last_refresh >= 21600 or engine.topics.refresh_requested and time.time() - last_refresh >= 300):
                        last_refresh = time.time()
                        engine.topics.refresh_requested = False
                        await collect_topics(engine.topics)
                    await worker.tick(active)
                else:
                    engine.store.set_option("background_status", "额度不足，暂停整理", invalidate=False)
        except Exception as exc:
            logger.warning("Persona background task recovered: {}", type(exc).__name__)
        await asyncio.sleep(60)
