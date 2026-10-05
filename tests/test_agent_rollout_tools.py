from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_offline_context_replay_has_strict_cross_api_prefix_and_full_tools():
    replay = load_script("replay_agent_context")

    report = replay.build_report(stable_chars=16_000)

    assert report["tool_count"] == 36
    assert report["cross_api_semantic_order_equal"] is True
    assert all(
        value["strict_append_prefix"] is True
        for value in report["api_styles"].values()
    )
    assert report["shadow"] == {
        "candidate_built_locally": True,
        "candidate_tool_count": 36,
        "legacy_provider_tool_count": 2,
        "additional_paid_requests": 0,
    }
    assert report["comparison"]["changed_suffix_reduction_percent"] >= 50


def test_context_replay_report_contains_no_synthetic_prompt_text():
    replay = load_script("replay_agent_context")

    encoded = json.dumps(replay.build_report(), ensure_ascii=False)

    assert "synthetic current input" not in encoded
    assert "stable synthetic context" not in encoded
    assert "delivered" not in encoded


def usage_record(**overrides):
    record = {
        "ts": "2026-09-21T10:00:00+08:00",
        "event": "reply",
        "model": "synthetic-model",
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "latency_ms": 200,
        "cache_status": "reported",
        "cache_read_tokens": 80,
        "cache_write_tokens": 0,
        "cache_miss_tokens": 20,
        "model_calls": 1,
        "tool_rounds": 0,
        "layout_version": "v2",
        "native_tool_mode": "true",
        "static_prefix_hash": "a" * 64,
        "tool_schema_hash": "b" * 64,
        "request_trace": "c" * 16,
    }
    record.update(overrides)
    return record


def test_usage_report_deduplicates_requests_and_preserves_unsupported():
    usage = load_script("report_agent_usage")
    records = [
        usage_record(event="model_started", prompt_tokens=0),
        usage_record(cache_read_tokens=70, cache_miss_tokens=30),
        usage_record(cache_read_tokens=80, cache_miss_tokens=20),
        usage_record(
            ts="2026-09-21T10:01:00+08:00",
            request_trace="d" * 16,
            cache_status="unsupported",
            cache_read_tokens=None,
            cache_write_tokens=None,
            cache_miss_tokens=None,
        ),
        usage_record(
            ts="2026-09-21T10:02:00+08:00",
            request_trace="e" * 16,
            cache_status="reported",
            cache_read_tokens=None,
            cache_write_tokens=None,
            cache_miss_tokens=None,
        ),
    ]

    report = usage.build_report(records)
    summary = report["windows"]["all"]

    assert summary["request_count"] == 3
    assert summary["cache"]["reported_records"] == 1
    assert summary["cache"]["unsupported_records"] == 2
    assert summary["cache"]["cache_read_tokens_total"] == 80
    assert summary["cache"]["non_cached_input_tokens_total"] == 20
    assert summary["cache"]["weighted_cache_ratio"] == 0.8
    assert summary["payload_builds"]["count"] == 1


def test_usage_report_keeps_privacy_safe_shadow_build_evidence_without_tokens():
    usage = load_script("report_agent_usage")
    build = usage_record(
        event="model_started",
        prompt_tokens=0,
        layout_version="shadow",
        native_tool_mode="shadow",
        shadow_payload_changed=True,
        native_tool_schema_hash="f" * 64,
        static_prefix_chars=1000,
        dynamic_status_chars=200,
        conversation_chars=2,
        detail="PRIVATE_BUILD_TEXT",
    )

    report = usage.build_report([build])["windows"]["all"]
    encoded = json.dumps(report)

    assert report["request_count"] == 0
    assert report["payload_builds"]["count"] == 1
    assert report["payload_builds"]["layouts"] == {"shadow": 1}
    assert report["payload_builds"]["shadow_comparisons"] == {"changed": 1}
    assert report["payload_builds"]["native_tool_schema_hashes"] == ["f" * 64]
    assert "PRIVATE_BUILD_TEXT" not in encoded


def test_usage_report_never_copies_text_identity_or_secret_fields():
    usage = load_script("report_agent_usage")
    record = usage_record(
        detail="PRIVATE_CHAT_TEXT",
        group_id="PRIVATE_GROUP_ID",
        user_id="PRIVATE_USER_ID",
        api_key="PRIVATE_API_KEY",
        request_trace="too-short",
    )

    encoded = json.dumps(usage.build_report([record]), ensure_ascii=False)

    assert "PRIVATE_" not in encoded
    assert "too-short" not in encoded
    assert "request_trace" not in encoded


def test_usage_report_supports_baseline_and_candidate_windows():
    usage = load_script("report_agent_usage")
    records = [
        usage_record(ts="2026-09-21T09:00:00+08:00", request_trace="a" * 16),
        usage_record(ts="2026-09-21T11:00:00+08:00", request_trace="b" * 16),
    ]

    report = usage.build_report(
        records,
        baseline_end=datetime.fromisoformat("2026-09-21T10:00:00+08:00"),
        candidate_start=datetime.fromisoformat("2026-09-21T10:00:00+08:00"),
    )

    assert report["windows"]["baseline"]["request_count"] == 1
    assert report["windows"]["candidate"]["request_count"] == 1


def test_usage_report_with_only_unsupported_cache_has_null_cache_totals():
    usage = load_script("report_agent_usage")
    record = usage_record(
        cache_status="unsupported",
        cache_read_tokens=None,
        cache_write_tokens=None,
        cache_miss_tokens=None,
    )

    cache = usage.build_report([record])["windows"]["all"]["cache"]

    assert cache["status"] == "unsupported"
    assert cache["reported_records"] == 0
    assert cache["cache_read_tokens_total"] is None
    assert cache["non_cached_input_tokens_total"] is None
    assert cache["weighted_cache_ratio"] is None


def test_usage_report_stratifies_group_spine_warm_requests_without_identity():
    usage = load_script("report_agent_usage")
    records = [
        usage_record(
            ts="2026-09-21T10:00:00+08:00",
            request_trace="a" * 16,
            session_scope="group_spine",
            cache_cohort_mode="on",
            cache_cohort_hash="d" * 16,
            context_epoch=1,
            budget_action="none",
        ),
        usage_record(
            ts="2026-09-21T10:01:00+08:00",
            request_trace="b" * 16,
            session_scope="group_spine",
            cache_cohort_mode="on",
            cache_cohort_hash="d" * 16,
            context_epoch=1,
            budget_action="hard_trimmed",
        ),
    ]

    summary = usage.build_report(records)["windows"]["all"]
    assert summary["session_scopes"] == {"group_spine": 2}
    assert summary["budget_actions"] == {"hard_trimmed": 1, "none": 1}
    assert summary["warm_group_spine"]["request_count"] == 1
    assert summary["warm_active_group_spine"]["request_count"] == 0
    assert "cache_cohort_hash" not in json.dumps(summary)


def test_usage_report_strict_warm_requires_active_affinity():
    usage = load_script("report_agent_usage")
    records = [
        usage_record(
            ts="2026-09-21T10:00:00+08:00", request_trace="a" * 16,
            session_scope="group_spine", cache_cohort_hash="d" * 16,
            cache_affinity_mode="active",
        ),
        usage_record(
            ts="2026-09-21T10:01:00+08:00", request_trace="b" * 16,
            session_scope="group_spine", cache_cohort_hash="d" * 16,
            cache_affinity_mode="off",
        ),
        usage_record(
            ts="2026-09-21T10:02:00+08:00", request_trace="c" * 16,
            session_scope="group_spine", cache_cohort_hash="d" * 16,
            cache_affinity_mode="active",
        ),
    ]
    summary = usage.build_report(records)["windows"]["all"]
    assert summary["warm_active_group_spine"]["request_count"] == 1


def test_cache_benchmark_is_offline_by_default_and_contains_no_provider_secrets():
    benchmark = load_script("benchmark_agent_cache")

    report = benchmark.offline_report(calls=3, stable_chars=16_000)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["mode"] == "offline"
    assert report["status"] == "not_run"
    assert report["network_requests"] == 0
    assert report["tool_count"] == 36
    assert report["stable_prefix_across_calls"] is True
    assert report["measured_cache_ratio"] is None
    assert "api_key" not in encoded
    assert "api_url" not in encoded
    assert "synthetic request" not in encoded


def test_cache_usage_row_does_not_turn_unsupported_into_zero_cache():
    benchmark = load_script("benchmark_agent_cache")

    row = benchmark._usage_row(
        1,
        {"prompt_tokens": 100, "cache_status": "unsupported"},
        warmup=True,
    )

    assert row["cache_status"] == "unsupported"
    assert "cache_read_tokens" not in row
    assert "cache_ratio" not in row


def test_provider_cache_probe_is_synthetic_and_offline_by_default():
    probe = load_script("probe_provider_cache")

    report = probe.offline_report(stable_chars=4096, ttl_seconds=60)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["mode"] == "offline"
    assert report["network_requests"] == 0
    assert report["vendor_fields_sent"] is False
    assert report["vendor_fields"] == []
    assert report["scenarios"]["strict_append"]["strict_prefix"] is True
    assert report["scenarios"]["cold"]["static_prefix_hash"] != report["scenarios"]["different_cohort"]["static_prefix_hash"]
    assert "api_key" not in encoded and "api_url" not in encoded


def test_provider_cache_probe_adds_affinity_only_when_explicitly_requested():
    from dataclasses import replace

    from bot.services.agent_context import ContextEnvelope
    from bot.services.tangtang_chat import TangtangConfig, TangtangProvider

    probe = load_script("probe_provider_cache")
    config = replace(
        TangtangConfig.disabled("test"),
        enabled=True,
        model="synthetic-model",
        max_output_tokens=32,
    )
    first = ContextEnvelope.create(
        persona="stable synthetic prefix",
        current_input="first request",
    )
    second = ContextEnvelope.create(
        persona="stable synthetic prefix",
        current_input="second request",
    )
    different = ContextEnvelope.create(
        persona="different synthetic prefix",
        current_input="control request",
    )

    default_payload = TangtangProvider._responses_payload(
        config, first.static_text, first.current_text, envelope=first
    )
    provider = probe.CacheProbeProvider(prompt_cache_key=True)
    first_payload = provider._responses_payload(
        config, first.static_text, first.current_text, envelope=first
    )
    second_payload = provider._responses_payload(
        config, second.static_text, second.current_text, envelope=second
    )
    different_payload = provider._responses_payload(
        config, different.static_text, different.current_text, envelope=different
    )
    chat_payload = provider._chat_payload(
        config, first.static_text, first.current_text, envelope=first
    )
    report = probe.offline_report(
        stable_chars=4096,
        ttl_seconds=60,
        prompt_cache_key=True,
    )

    assert "prompt_cache_key" not in default_payload
    assert first_payload["prompt_cache_key"] == second_payload["prompt_cache_key"]
    assert first_payload["prompt_cache_key"] != different_payload["prompt_cache_key"]
    assert chat_payload["prompt_cache_key"] == first_payload["prompt_cache_key"]
    assert report["vendor_fields_sent"] is True
    assert report["vendor_fields"] == ["prompt_cache_key"]
    assert first_payload["prompt_cache_key"] not in json.dumps(report)


def test_live_cache_benchmark_writes_bounded_http_failure_evidence(monkeypatch):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace

    import httpx

    from bot.services.tangtang_chat import TangtangConfig

    benchmark = load_script("benchmark_agent_cache")
    config = replace(
        TangtangConfig.disabled("test"),
        enabled=True,
        api_style="chat_completions",
        model="synthetic-model",
        max_output_tokens=32,
        max_response_chars=80,
        timeout_seconds=5,
    )

    class BrokenProvider:
        async def generate_agent(self, *_args, **_kwargs):
            request = httpx.Request("POST", "http://127.0.0.1:1/v1/chat/completions")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    monkeypatch.setattr(benchmark, "config_loader", SimpleNamespace(load=lambda: config))
    monkeypatch.setattr(benchmark, "TangtangProvider", BrokenProvider)

    report = asyncio.run(benchmark.live_report(calls=3, stable_chars=4096))

    assert report["status"] == "failed"
    assert report["network_requests"] == 1
    assert report["completed_calls"] == 0
    assert report["http_status"] == 502
    assert report["measured_cache_ratio"] is None
    assert "http://" not in json.dumps(report)


def test_rollout_configuration_changes_only_the_fixed_rollout_switches():
    rollout = load_script("configure_agent_rollout")
    original = [
        "SECRET_KEY=do-not-touch",
        "TANGTANG_CONTEXT_LAYOUT=v1",
        "TANGTANG_NATIVE_ACTION_TOOLS=false",
        "TAIL=value",
    ]

    output, changes = rollout.updated_lines(original, "shadow")

    assert "SECRET_KEY=do-not-touch" in output
    assert "TAIL=value" in output
    assert "TANGTANG_CONTEXT_LAYOUT=shadow" in output
    assert "TANGTANG_NATIVE_ACTION_TOOLS=shadow" in output
    assert "TANGTANG_CONTEXT_COMPACTION_ENABLED=false" in output
    assert "TANGTANG_CACHE_COHORT_MODE=off" in output
    assert "TANGTANG_CACHE_CANARY_GROUP_IDS=" in output
    assert set(changes) == set(rollout.SWITCH_KEYS)


def test_rollout_configuration_rejects_duplicate_switches():
    rollout = load_script("configure_agent_rollout")

    try:
        rollout.updated_lines(
            ["TANGTANG_CONTEXT_LAYOUT=v1", "TANGTANG_CONTEXT_LAYOUT=v2"],
            "rollback",
        )
    except ValueError as exc:
        assert "duplicate rollout key" in str(exc)
    else:
        raise AssertionError("duplicate rollout keys must be rejected")


def test_rollout_apply_is_atomic_and_creates_a_recoverable_backup(tmp_path):
    rollout = load_script("configure_agent_rollout")
    env_path = tmp_path / ".env"
    backup_dir = tmp_path / "backups"
    original = "SECRET_KEY=do-not-touch\n"
    env_path.write_text(original, encoding="utf-8")

    backup, changes = rollout.apply_mode(env_path, "v2", backup_dir=backup_dir)

    assert backup is not None and backup.read_text(encoding="utf-8") == original
    assert set(changes) == set(rollout.SWITCH_KEYS)
    updated = env_path.read_text(encoding="utf-8")
    assert "SECRET_KEY=do-not-touch" in updated
    assert "TANGTANG_CONTEXT_LAYOUT=v2" in updated
    assert "TANGTANG_NATIVE_ACTION_TOOLS=true" in updated
    assert "TANGTANG_CONTEXT_COMPACTION_ENABLED=true" in updated
    assert "TANGTANG_CACHE_COHORT_MODE=off" in updated


def test_cache_canary_requires_explicit_positive_group_ids():
    rollout = load_script("configure_agent_rollout")

    try:
        rollout.updated_lines([], "cache-canary")
    except ValueError as exc:
        assert "canary-group-ids" in str(exc)
    else:
        raise AssertionError("canary rollout must require a group list")

    output, changes = rollout.updated_lines([], "cache-canary", canary_group_ids="1001, 1002")
    assert "TANGTANG_CACHE_COHORT_MODE=canary" in output
    assert "TANGTANG_CACHE_CANARY_GROUP_IDS=1001,1002" in output
    assert changes["TANGTANG_CACHE_COHORT_MODE"] == ("<unset>", "canary")


def test_rollout_output_redacts_canary_group_ids(tmp_path, monkeypatch, capsys):
    rollout = load_script("configure_agent_rollout")
    env_path = tmp_path / ".env"
    env_path.write_text("TANGTANG_CONTEXT_LAYOUT=v2\n", encoding="utf-8")
    monkeypatch.setattr(rollout, "ENV_PATH", env_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "configure_agent_rollout.py",
            "--mode",
            "cache-canary",
            "--canary-group-ids",
            "1001,1002",
        ],
    )

    assert rollout.main() == 0
    output = capsys.readouterr().out
    assert "1001" not in output and "1002" not in output
    assert "TANGTANG_CACHE_CANARY_GROUP_IDS: <empty> -> <configured:2>" in output
