"""Tests for TangtangCircuitBreaker (Phase 3 / Q11)."""

from __future__ import annotations

import time
import pytest
from bot.services.tangtang_circuit_breaker import (
    CircuitState,
    TangtangCircuitBreaker,
)
from bot.services.tangtang_models import TangtangModelCatalog, TangtangModelProfile


def _sample_catalog() -> TangtangModelCatalog:
    p3 = TangtangModelProfile(
        name="模型3",
        provider="custom",
        api_url="https://example.invalid/v1",
        api_key="key3",
        api_style="responses",
        model="gpt-5.6-luna",
        reasoning_effort="none",
    )
    p4 = TangtangModelProfile(
        name="模型4",
        provider="custom",
        api_url="https://example.invalid/v1",
        api_key="key4",
        api_style="chat_completions",
        model="gemini-2.5-flash",
        reasoning_effort="none",
    )
    p1 = TangtangModelProfile(
        name="模型1",
        provider="custom",
        api_url="https://example.invalid/v1",
        api_key="key1",
        api_style="chat_completions",
        model="deepseek-chat",
        reasoning_effort="none",
    )
    return TangtangModelCatalog(profiles=(p3, p4, p1), active_name="模型3")


def test_initial_state_and_success():
    cb = TangtangCircuitBreaker(failure_threshold=3, window_seconds=60, cool_down_seconds=10)
    assert cb.state == CircuitState.CLOSED
    assert cb.is_available() is True

    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_trips_to_open_on_threshold_failures():
    cb = TangtangCircuitBreaker(failure_threshold=3, window_seconds=60, cool_down_seconds=10)

    # 1st failure
    assert cb.record_failure(502) is False
    assert cb.state == CircuitState.CLOSED

    # 2nd failure
    assert cb.record_failure(TimeoutError("timeout")) is False
    assert cb.state == CircuitState.CLOSED

    # 3rd failure -> trips to OPEN
    assert cb.record_failure(504) is True
    assert cb.state == CircuitState.OPEN
    assert cb.is_available() is False


def test_non_eligible_errors_ignored():
    cb = TangtangCircuitBreaker(failure_threshold=3, window_seconds=60, cool_down_seconds=10)

    # 400 Bad Request should not trip
    assert cb.record_failure(400) is False
    assert cb.record_failure(404) is False
    assert cb.state == CircuitState.CLOSED


def test_half_open_recovery_flow():
    cb = TangtangCircuitBreaker(
        failure_threshold=2,
        window_seconds=60,
        cool_down_seconds=0.2,
        half_open_success_threshold=2,
    )

    cb.record_failure(502)
    cb.record_failure(503)
    assert cb.state == CircuitState.OPEN

    # Wait for cool down
    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.is_available() is True

    # 1st success in half open
    cb.record_success()
    assert cb.state == CircuitState.HALF_OPEN

    # 2nd success in half open -> recovers to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.is_available() is True


def test_half_open_failure_immediately_retrips():
    cb = TangtangCircuitBreaker(
        failure_threshold=2,
        window_seconds=60,
        cool_down_seconds=0.2,
        half_open_success_threshold=2,
    )

    cb.record_failure(502)
    cb.record_failure(503)
    assert cb.state == CircuitState.OPEN

    time.sleep(0.25)
    assert cb.state == CircuitState.HALF_OPEN

    # Failure in HALF_OPEN trips immediately back to OPEN
    assert cb.record_failure(502) is True
    assert cb.state == CircuitState.OPEN


def test_resolve_fallback_profile():
    cb = TangtangCircuitBreaker()
    catalog = _sample_catalog()

    fallback = cb.resolve_fallback_profile(catalog)
    assert fallback is not None
    assert fallback.name == "模型4"
    assert fallback.model == "gemini-2.5-flash"
