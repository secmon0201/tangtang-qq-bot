"""Automatic draft/review/publication, bounded and independent of chat delivery."""
import asyncio
from dataclasses import replace
import json
import time

from bot.services.persona_background_retry import BackgroundRetry
from bot.services.persona_capacity import background_request
from bot.services.persona_profile_contract import DRAFT_INSTRUCTION, REVIEW_INSTRUCTION, decode, validate_draft
from bot.services.persona_profile_store import ProfileStore


DRAFT_MAX_OUTPUT_TOKENS = 8000
DRAFT_MAX_RESPONSE_CHARS = 32000
REVIEW_MAX_OUTPUT_TOKENS = 6000
REVIEW_MAX_RESPONSE_CHARS = 24000
DRAFT_ESCALATED_OUTPUT_TOKENS = 16000
DRAFT_ESCALATED_RESPONSE_CHARS = 48000
REVIEW_ESCALATED_OUTPUT_TOKENS = 9000
REVIEW_ESCALATED_RESPONSE_CHARS = 36000
_NON_TERMINAL_PROFILE_ERRORS = {
    'profile_gate_changed',
    'profile_lease_changed',
    'profile_draft_missing',
    'profile_source_changed',
    'profile_quote_not_in_original',
}


def _truncated_output(error):
    return any(marker in error for marker in ('Unterminated string', 'Expecting value', 'Expecting property', 'Expecting \',\''))


class ProfileWorker:
    def __init__(self, cognition, central, provider, loader):
        self.profiles = ProfileStore(cognition)
        self.central, self.provider, self.loader = central, provider, loader
        self.retry = BackgroundRetry(central, key='profile_provider_retry', base_delay=10, max_delay=21600)
        self.lock = asyncio.Lock()

    async def tick(self):
        if self.lock.locked():
            return
        config = replace(self.loader.load(), timeout_seconds=30, reasoning_effort='none')
        draft_config = replace(config, max_output_tokens=DRAFT_MAX_OUTPUT_TOKENS,
                               max_response_chars=DRAFT_MAX_RESPONSE_CHARS)
        review_config = replace(config, max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                                max_response_chars=REVIEW_MAX_RESPONSE_CHARS)
        enabled = lambda: (self.central.option('denia_v2_enabled', False)
                           and self.central.option('background_enabled', True))
        if not config.enabled or not config.memory_enabled or not enabled() or self.retry.blocked_status(config, time.time()):
            return
        async with self.lock:
            batch = await asyncio.to_thread(self.profiles.claim)
            if batch is None:
                return
            token = background_request.set(True)
            stage = batch.stage
            try:
                async def generate(target, name, instruction, prompt):
                    raw, usage = await asyncio.wait_for(self.provider.generate(target, instruction, prompt), 45)
                    self.central.record_job('profile_' + name, 0, time.time(), usage, 'returned')
                    return decode(raw)
                truncated = _truncated_output(batch.previous_error)
                if batch.stage == 'draft':
                    active_draft_config = replace(
                        draft_config,
                        max_output_tokens=(DRAFT_ESCALATED_OUTPUT_TOKENS if truncated
                                           else DRAFT_MAX_OUTPUT_TOKENS),
                        max_response_chars=(DRAFT_ESCALATED_RESPONSE_CHARS if truncated
                                            else DRAFT_MAX_RESPONSE_CHARS),
                    )
                    draft = await generate(active_draft_config, 'draft', DRAFT_INSTRUCTION, batch.prompt())
                    validate_draft(draft, batch)
                    await asyncio.to_thread(self.profiles.save_draft, batch, draft)
                else:
                    draft = batch.draft
                    if draft is None:
                        raise ValueError('profile_draft_missing')
                active_review_config = replace(
                    review_config,
                    max_output_tokens=(REVIEW_ESCALATED_OUTPUT_TOKENS if batch.stage == 'review' and truncated
                                       else REVIEW_MAX_OUTPUT_TOKENS),
                    max_response_chars=(REVIEW_ESCALATED_RESPONSE_CHARS if batch.stage == 'review' and truncated
                                        else REVIEW_MAX_RESPONSE_CHARS),
                )
                stage = 'review'
                review = await generate(active_review_config, 'review', REVIEW_INSTRUCTION,
                    batch.prompt() + '\n待独立复核的draft：\n' + json.dumps(draft, ensure_ascii=False))
                if not enabled():
                    raise ValueError('profile_gate_changed')
                stage = 'publish'
                await asyncio.to_thread(self.profiles.publish, batch, draft, review)
                self.retry.succeeded()
                self.central.record_job('profile_publish', 0, time.time(), {}, 'completed')
            except asyncio.CancelledError:
                await asyncio.to_thread(self.profiles.fail, batch, 'CancelledError', None, terminal=False)
                raise
            except Exception as exc:
                detail = str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
                if not isinstance(exc, (ValueError, TypeError, KeyError)):
                    self.retry.failed(config, exc, time.time())
                terminal = (isinstance(exc, (ValueError, TypeError, KeyError))
                            and detail not in _NON_TERMINAL_PROFILE_ERRORS)
                await asyncio.to_thread(self.profiles.fail, batch, detail, None, terminal=terminal)
                self.central.record_job('profile_' + stage, 0, time.time(), {}, type(exc).__name__)
            finally:
                background_request.reset(token)
