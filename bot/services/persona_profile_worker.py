"""Automatic draft/review/publication, bounded and independent of chat delivery."""
import asyncio
from dataclasses import replace
import json
import time

from bot.services.persona_background_retry import BackgroundRetry
from bot.services.persona_capacity import background_request
from bot.services.persona_profile_contract import DRAFT_INSTRUCTION, REVIEW_INSTRUCTION, decode, validate_draft
from bot.services.persona_profile_store import ProfileStore


class ProfileWorker:
    def __init__(self, cognition, central, provider, loader):
        self.profiles = ProfileStore(cognition)
        self.central, self.provider, self.loader = central, provider, loader
        self.retry = BackgroundRetry(central, key='profile_provider_retry', base_delay=10, max_delay=300)
        self.lock = asyncio.Lock()

    async def tick(self):
        if self.lock.locked():
            return
        config = replace(self.loader.load(), max_output_tokens=3200, max_response_chars=18000,
                         timeout_seconds=30, reasoning_effort='none')
        enabled = lambda: (self.central.option('denia_v2_enabled', False)
                           and self.central.option('background_enabled', True))
        if not config.enabled or not config.memory_enabled or not enabled() or self.retry.blocked_status(config, time.time()):
            return
        async with self.lock:
            batch = await asyncio.to_thread(self.profiles.claim)
            if batch is None:
                return
            token = background_request.set(True)
            try:
                async def generate(stage, instruction, prompt):
                    raw, usage = await asyncio.wait_for(self.provider.generate(config, instruction, prompt), 45)
                    self.central.record_job('profile_' + stage, 0, time.time(), usage, 'returned')
                    return decode(raw)
                draft = await generate('draft', DRAFT_INSTRUCTION, batch.prompt())
                validate_draft(draft, batch)
                review = await generate('review', REVIEW_INSTRUCTION,
                    batch.prompt() + '\n待独立复核的draft：\n' + json.dumps(draft, ensure_ascii=False))
                if not enabled():
                    raise ValueError('profile_gate_changed')
                await asyncio.to_thread(self.profiles.publish, batch, draft, review)
                self.retry.succeeded()
                self.central.record_job('profile_publish', 0, time.time(), {}, 'completed')
            except asyncio.CancelledError:
                await asyncio.to_thread(self.profiles.fail, batch, 'CancelledError')
                raise
            except Exception as exc:
                detail = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
                await asyncio.to_thread(self.profiles.fail, batch, detail)
                if not isinstance(exc, (ValueError, TypeError, KeyError)):
                    self.retry.failed(config, exc, time.time())
                self.central.record_job('profile_publish', 0, time.time(), {}, type(exc).__name__)
            finally:
                background_request.reset(token)
