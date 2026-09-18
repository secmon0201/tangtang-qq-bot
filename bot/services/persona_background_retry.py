"""Persisted provider circuit and allowlisted diagnostics for background jobs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx


_STATE_KEY = "background_provider_retry"
_KNOWN_CODES = {
    "invalid_api_key", "authentication_error", "permission_denied", "model_not_found",
    "invalid_request_error", "unsupported_parameter", "rate_limit_exceeded",
    "rate_limit_error", "insufficient_quota", "server_error", "service_unavailable",
    "overloaded_error", "timeout", "context_length_exceeded",
}


@dataclass(frozen=True)
class BackgroundFailure:
    reason: str
    retryable: bool
    status: int | None = None
    code: str = ""
    retry_after: float = 0.0


def _retry_after(response: httpx.Response, now: float) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            stamp = parsedate_to_datetime(value)
            seconds = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, min(seconds, 21600.0))


def classify_background_failure(exc: Exception, now: float) -> BackgroundFailure:
    # Never persist str(exc), URL, response body, request, or arbitrary provider
    # codes. A compatible endpoint can reflect private prompts into any of them.
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code = "unclassified"
        try:
            error = exc.response.json().get("error", {})
            candidate = error.get("code") or error.get("type") if isinstance(error, dict) else None
            if candidate in _KNOWN_CODES:
                code = candidate
        except (ValueError, AttributeError, TypeError):
            pass
        retryable = (status in {408, 409, 425, 429} or status >= 500) and code != "insufficient_quota"
        return BackgroundFailure("provider_http", retryable, status, code, _retry_after(exc.response, now))
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return BackgroundFailure("provider_timeout", True)
    if isinstance(exc, httpx.TransportError):
        return BackgroundFailure("provider_network", True)
    if isinstance(exc, (ValueError, TypeError)):
        return BackgroundFailure("invalid_model_output", True)
    return BackgroundFailure("background_internal_error", False)


class BackgroundRetry:
    """One shared circuit across groups; every actual retry still claims budget."""

    def __init__(self, store, *, key=_STATE_KEY, base_delay=300, max_delay=21600) -> None:
        self.store = store
        self.key, self.base_delay, self.max_delay = key, base_delay, max_delay

    @staticmethod
    def configuration_key(config) -> str:
        relevant = (config.api_url, config.api_style, config.api_key, config.model,
                    config.reasoning_effort, config.max_output_tokens)
        return hashlib.sha256(json.dumps(relevant).encode()).hexdigest()

    def state(self, config) -> dict:
        state = self.store.option(self.key, {})
        return state if state.get("configuration") == self.configuration_key(config) else {}

    def blocked_status(self, config, now: float) -> str:
        state = self.state(config)
        pending = state.get("next_attempt_at", 0) > now
        if state.get("blocked_until_config_change") and pending:
            return "模型服务配置、权限或额度错误，等待配置修复或每日复检（待整理证据保留）"
        if pending:
            return "模型服务暂时不可用，退避等待重试（待整理证据保留）"
        return ""

    def failed(self, config, exc: Exception, now: float) -> dict:
        failure = classify_background_failure(exc, now)
        count = min(int(self.state(config).get("failures", 0)) + 1, 8)
        delay = min(self.max_delay, max(self.base_delay * 2 ** (count - 1), failure.retry_after))
        detail = {
            "reason": failure.reason, "http_status": failure.status,
            "error_code": failure.code, "retryable": failure.retryable,
            "next_attempt_at": now + (delay if failure.retryable else 86400),
        }
        self.store.set_option(self.key, {
            **detail, "configuration": self.configuration_key(config), "failures": count,
            "blocked_until_config_change": not failure.retryable,
        }, invalidate=False)
        return detail

    def succeeded(self) -> None:
        self.store.set_option(self.key, {}, invalidate=False)


def parse_growth_output(output: str) -> list:
    """Accept JSON fences, without extracting a convenient object from prose."""
    clean = output.strip()
    if clean.startswith("```json\n") and clean.endswith("```"):
        clean = clean[8:-3].strip()
    elif clean.startswith("```\n") and clean.endswith("```"):
        clean = clean[4:-3].strip()
    parsed = json.loads(clean)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("proposals"), list):
        raise ValueError("invalid_proposals_format")
    return parsed["proposals"]
