from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx
import truststore
from dotenv import dotenv_values
from nonebot import logger

from bot.config import ROOT


truststore.inject_into_ssl()


def _raw(values: Mapping[str, Any], name: str, default: str = "") -> str:
    value = values.get(name)
    return default if value is None else str(value).strip()


def _bool(values: Mapping[str, Any], name: str, default: bool) -> bool:
    value = _raw(values, name, "true" if default else "false").lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _int(
    values: Mapping[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = int(_raw(values, name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _pick(values: Mapping[str, Any], profile_name: str, tangtang_name: str, default: str) -> str:
    raw = _raw(values, profile_name)
    if raw:
        return raw
    return _raw(values, tangtang_name, default)


_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _retry_delay(retry_after: str | None, attempt: int, base_seconds: float) -> float:
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    return base_seconds * (2 ** (attempt - 1)) + random.uniform(0.0, 1.0)


@dataclass(frozen=True, slots=True)
class TangtangProfileConfig:
    enabled: bool
    api_url: str
    api_key: str
    api_style: str
    model: str
    reasoning_effort: str
    timeout_seconds: int
    max_input_chars: int
    max_output_tokens: int
    max_response_chars: int
    chunk_chars: int
    max_records_per_run: int
    max_concurrent: int
    retry_max_attempts: int
    retry_base_seconds: float
    merge_chunk_chars: int
    evidence_concurrency: int
    evidence_reasoning_effort: str
    evidence_max_chars: int
    evidence_max_tokens: int
    evidence_response_chars: int
    final_max_chars: int
    final_max_tokens: int
    final_response_chars: int
    disabled_reason: str = ""

    @classmethod
    def disabled(cls, reason: str = "PROFILE_ENABLED=false") -> "TangtangProfileConfig":
        return cls(
            enabled=False,
            api_url="",
            api_key="",
            api_style="responses",
            model="",
            reasoning_effort="none",
            timeout_seconds=0,
            max_input_chars=0,
            max_output_tokens=0,
            max_response_chars=0,
            chunk_chars=0,
            max_records_per_run=0,
            max_concurrent=0,
            retry_max_attempts=0,
            retry_base_seconds=0.0,
            merge_chunk_chars=0,
            evidence_concurrency=0,
            evidence_reasoning_effort="none",
            evidence_max_chars=0,
            evidence_max_tokens=0,
            evidence_response_chars=0,
            final_max_chars=0,
            final_max_tokens=0,
            final_response_chars=0,
            disabled_reason=reason,
        )

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> "TangtangProfileConfig":
        tangtang_enabled = _bool(values, "TANGTANG_ENABLED", False)
        enabled = _bool(values, "PROFILE_ENABLED", tangtang_enabled)
        api_style = _pick(values, "PROFILE_API_STYLE", "TANGTANG_API_STYLE", "responses").lower()
        if api_style not in {"responses", "chat_completions"}:
            raise ValueError("PROFILE_API_STYLE must be responses or chat_completions")
        reasoning_effort = _pick(
            values, "PROFILE_REASONING_EFFORT", "TANGTANG_REASONING_EFFORT", "none"
        ).lower()
        if reasoning_effort not in {"none", "low", "high", "max"}:
            raise ValueError("PROFILE_REASONING_EFFORT is invalid")
        evidence_reasoning_effort = _raw(
            values, "PROFILE_EVIDENCE_REASONING_EFFORT", "low"
        ).lower()
        if evidence_reasoning_effort not in {"none", "low", "high", "max"}:
            raise ValueError("PROFILE_EVIDENCE_REASONING_EFFORT is invalid")
        api_url = _pick(values, "PROFILE_API_URL", "TANGTANG_API_URL", "")
        api_key = _pick(values, "PROFILE_API_KEY", "TANGTANG_API_KEY", "")
        model = _pick(values, "PROFILE_MODEL", "TANGTANG_MODEL", "")
        if not api_url or not api_key or not model:
            raise ValueError(
                "PROFILE_API_URL, PROFILE_API_KEY and PROFILE_MODEL are required "
                "(or their TANGTANG_* equivalents)"
            )
        max_input_chars = _int(values, "PROFILE_MAX_INPUT_CHARS", 20000, 1000, 64000)
        chunk_chars = _int(values, "PROFILE_CHUNK_CHARS", 16000, 1000, 64000)
        merge_chunk_chars = _int(
            values, "PROFILE_MERGE_CHUNK_CHARS", 12000, 1000, 64000
        )
        if chunk_chars + 1000 > max_input_chars:
            raise ValueError(
                "PROFILE_CHUNK_CHARS must leave at least 1000 chars below "
                "PROFILE_MAX_INPUT_CHARS"
            )
        if merge_chunk_chars + 1000 > max_input_chars:
            raise ValueError(
                "PROFILE_MERGE_CHUNK_CHARS must leave at least 1000 chars below "
                "PROFILE_MAX_INPUT_CHARS"
            )
        return cls(
            enabled=enabled,
            api_url=api_url,
            api_key=api_key,
            api_style=api_style,
            model=model,
            reasoning_effort=reasoning_effort,
            timeout_seconds=_int(values, "PROFILE_TIMEOUT_SECONDS", 120, 1, 600),
            max_input_chars=max_input_chars,
            max_output_tokens=_int(values, "PROFILE_MAX_OUTPUT_TOKENS", 16000, 16, 24000),
            max_response_chars=_int(values, "PROFILE_MAX_RESPONSE_CHARS", 16000, 40, 24000),
            chunk_chars=chunk_chars,
            max_records_per_run=_int(
                values, "PROFILE_MAX_RECORDS_PER_RUN", 100000, 100, 500000
            ),
            max_concurrent=_int(values, "PROFILE_MAX_CONCURRENT", 3, 1, 16),
            retry_max_attempts=_int(
                values, "PROFILE_RETRY_MAX_ATTEMPTS", 3, 1, 10
            ),
            retry_base_seconds=float(
                _int(values, "PROFILE_RETRY_BASE_SECONDS", 2, 0, 60)
            ),
            merge_chunk_chars=merge_chunk_chars,
            evidence_concurrency=_int(
                values, "PROFILE_EVIDENCE_CONCURRENCY", 5, 1, 16
            ),
            evidence_reasoning_effort=evidence_reasoning_effort,
            evidence_max_chars=_int(
                values, "PROFILE_EVIDENCE_MAX_CHARS", 4000, 100, 12000
            ),
            evidence_max_tokens=_int(
                values, "PROFILE_EVIDENCE_MAX_TOKENS", 8000, 64, 24000
            ),
            evidence_response_chars=_int(
                values, "PROFILE_EVIDENCE_MAX_RESPONSE_CHARS", 16000, 200, 24000
            ),
            final_max_chars=_int(values, "PROFILE_FINAL_MAX_CHARS", 4000, 100, 8000),
            final_max_tokens=_int(
                values, "PROFILE_FINAL_MAX_TOKENS", 8000, 64, 24000
            ),
            final_response_chars=_int(
                values, "PROFILE_FINAL_MAX_RESPONSE_CHARS", 16000, 200, 24000
            ),
        )


class TangtangProfileConfigLoader:
    """Hot-reloadable PROFILE_* loader; API settings fall back to TANGTANG_*."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (ROOT / ".env")
        self._signature: tuple[int, int] | None = None
        self._config = TangtangProfileConfig.disabled()

    def load(self) -> TangtangProfileConfig:
        try:
            stat = self.path.stat()
        except OSError:
            return self._config
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return self._config
        self._signature = signature
        try:
            values = dotenv_values(self.path)
            self._config = TangtangProfileConfig.from_values(values)
            logger.info(
                f"Tangtang profile configuration reloaded; enabled={self._config.enabled}"
            )
        except (OSError, TypeError, ValueError) as exc:
            self._config = TangtangProfileConfig.disabled("configuration_error")
            logger.warning(f"Tangtang profile configuration invalid; disabled: {exc}")
        return self._config


class TangtangProfileProvider:
    @staticmethod
    def endpoint(config: TangtangProfileConfig) -> str:
        url = config.api_url.rstrip("/")
        suffix = "/responses" if config.api_style == "responses" else "/chat/completions"
        return url if url.endswith(suffix) else url + suffix

    @staticmethod
    def _payload(
        config: TangtangProfileConfig,
        system_prompt: str,
        user_prompt: str,
        *,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        effective_reasoning = reasoning_effort or config.reasoning_effort
        if config.api_style == "responses":
            payload: dict[str, Any] = {
                "model": config.model,
                "input": [
                    {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                    {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
                ],
                "max_output_tokens": config.max_output_tokens,
                "reasoning": {"effort": effective_reasoning},
            }
        else:
            payload = {
                "model": config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": config.max_output_tokens,
                "thinking": {
                    "type": "enabled" if effective_reasoning != "none" else "disabled"
                },
            }
            if effective_reasoning != "none":
                payload["reasoning_effort"] = effective_reasoning
        return payload

    @staticmethod
    def _extract_text(data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        output = data.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "output_text"
                    ):
                        return str(part.get("text") or "")
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                return str(message.get("content") or "")
        return ""

    async def generate(
        self,
        config: TangtangProfileConfig,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
        max_response_chars: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        if len(user_prompt) > config.max_input_chars:
            user_prompt = user_prompt[-config.max_input_chars:]
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "X-Request-Timeout-Ms": str(min(config.timeout_seconds, 30) * 1000),
        }
        effective_tokens = (
            config.max_output_tokens
            if max_output_tokens is None
            else min(max_output_tokens, config.max_output_tokens)
        )
        effective_chars = (
            config.max_response_chars
            if max_response_chars is None
            else min(max_response_chars, config.max_response_chars)
        )
        payload = self._payload(
            config, system_prompt, user_prompt, reasoning_effort=reasoning_effort
        )
        if config.api_style == "responses":
            payload["max_output_tokens"] = effective_tokens
        else:
            payload["max_tokens"] = effective_tokens
        started = time.monotonic()
        attempts = max(1, config.retry_max_attempts)
        data: Any = None
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    response = await client.post(
                        self.endpoint(config), headers=headers, json=payload
                    )
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                    delay = _retry_delay(
                        response.headers.get("retry-after"),
                        attempt,
                        config.retry_base_seconds,
                    )
                    logger.warning(
                        "Tangtang profile request failed: status={} attempt={}/{} "
                        "retry_in={:.1f}s",
                        response.status_code,
                        attempt,
                        attempts,
                        delay,
                    )
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                data = response.json()
                break
            except Exception as exc:
                logger.warning("Tangtang profile request failed: {}", exc)
                raise
        text = self._extract_text(data)[:effective_chars].strip()
        logger.info(
            "Tangtang profile request done; chars={}, latency_ms={}",
            len(text),
            round((time.monotonic() - started) * 1000),
        )
        return text


class TangtangProfileService:
    def __init__(
        self,
        loader: TangtangProfileConfigLoader | None = None,
        provider: TangtangProfileProvider | None = None,
    ) -> None:
        self.loader = loader or TangtangProfileConfigLoader()
        self.provider = provider or TangtangProfileProvider()

    def config(self) -> TangtangProfileConfig:
        return self.loader.load()

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
        max_response_chars: int | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        config = self.config()
        if not config.enabled:
            raise RuntimeError("PROFILE_ENABLED=false; profile AI is disabled")
        return await self.provider.generate(
            config,
            system_prompt,
            user_prompt,
            max_output_tokens=max_output_tokens,
            max_response_chars=max_response_chars,
            reasoning_effort=reasoning_effort,
        )


class ProfileRunGate:
    """Per-user exclusion plus a configurable global concurrency limit.

    A user may only have one queued/active profile run; when the global limit
    is reached, extra runs wait FIFO instead of hitting the API in parallel.
    """

    def __init__(self) -> None:
        self._active_users: set[int] = set()
        self._queued_users: set[int] = set()
        self._running = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def active_count(self) -> int:
        return self._running

    @property
    def queue_depth(self) -> int:
        return len(self._waiters)

    def is_user_pending(self, user_id: int) -> bool:
        user_id = int(user_id)
        return user_id in self._active_users or user_id in self._queued_users

    async def acquire(self, user_id: int, max_concurrent: int) -> bool:
        user_id = int(user_id)
        if self.is_user_pending(user_id):
            return False
        loop = asyncio.get_running_loop()
        self._queued_users.add(user_id)
        try:
            while self._running >= max(1, int(max_concurrent)):
                waiter = loop.create_future()
                self._waiters.append(waiter)
                try:
                    await waiter
                except asyncio.CancelledError:
                    self._waiters.remove(waiter)
                    raise
            self._active_users.add(user_id)
            self._running += 1
            return True
        finally:
            self._queued_users.discard(user_id)

    def release(self, user_id: int) -> None:
        self._active_users.discard(int(user_id))
        if self._running > 0:
            self._running -= 1
        if self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
