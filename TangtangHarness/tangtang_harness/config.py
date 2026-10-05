"""Configuration confined to TangtangHarness; never loads the legacy .env."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .redaction import redact_payload


DEFAULT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class ModelProfile:
    id: str
    name: str
    provider: str
    model: str
    base_url: str
    api_key: str = ""
    api_key_env: str = ""
    api_style: str = "chat_completions"
    output_token_field: str = 'max_completion_tokens'
    context_limit: int | None = 131_072
    max_output_tokens: int = 4_096
    reasoning_effort: str = "none"
    cache_key_enabled: bool = False
    vision: bool = True
    timeout_seconds: float = 120.0
    fallback_profile_id: str = ""
    account_label: str = ""
    extra_body: dict[str, Any] = field(default_factory=dict)
    usage_semantics: str = "auto"
    tokenizer: str = ""
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cache_read_price_per_million: float | None = None
    cache_write_price_per_million: float | None = None
    currency: str = ""
    price_version: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ModelProfile:
        value = dict(raw)
        value.setdefault("id", str(value.get("name", "model")))
        value.setdefault("name", value["id"])
        value.setdefault("provider", "custom")
        value.setdefault("model", "")
        value.setdefault("base_url", "")
        value.setdefault("context_limit", None)
        names = cls.__dataclass_fields__
        result = cls(**{key: val for key, val in value.items() if key in names})
        if result.api_style not in {"chat_completions", "responses"}:
            raise ValueError("api_style 必须是 chat_completions 或 responses")
        if result.output_token_field not in {'max_completion_tokens', 'max_tokens'}:
            raise ValueError('output_token_field 必须是 max_completion_tokens 或 max_tokens')
        if result.context_limit is not None and result.context_limit <= result.max_output_tokens + 1024:
            raise ValueError("模型上下文容量必须大于输出预算加 1024 token")
        return result


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    root: Path = DEFAULT_ROOT
    host: str = "127.0.0.1"
    port: int = 8090
    mode: str = "observe"
    profiles: tuple[ModelProfile, ...] = ()
    active_model: str = ""
    input_budget_tokens: int = 32_768
    cache_input_budget_tokens: int | None = None
    soft_budget_ratio: float = 0.75
    hard_budget_ratio: float = 0.90
    recent_rounds: int = 8
    persona: str = "denia"
    call_keyword: str = "娅娅"
    group_ids: tuple[int, ...] = ()
    onebot_url: str = "ws://127.0.0.1:3001"
    onebot_access_token: str = ""
    private_chat_enabled: bool = True
    mention_chat_enabled: bool = True
    proactive_enabled: bool = False
    continuation_enabled: bool = True
    memory_enabled: bool = True
    cognition_enabled: bool = True
    growth_enabled: bool = True
    profile_enabled: bool = False
    summary_enabled: bool = True
    compaction_enabled: bool = True
    cache_warmer_enabled: bool = False
    speech_enabled: bool = False
    humanize_enabled: bool = True
    max_reply_bubbles: int = 6
    max_reply_chars: int = 4_000
    background_batch_size: int = 5
    background_enabled: bool = True
    background_requests_per_hour: int = 20
    background_tokens_per_hour: int = 60_000
    background_input_budget_tokens: int = 8_192
    background_max_output_tokens: int = 800
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def active_profile(self) -> ModelProfile | None:
        return next((profile for profile in self.profiles if profile.id == self.active_model), None)

    def profile(self, profile_id: str | None = None) -> ModelProfile:
        target = profile_id or self.active_model
        profile = next((profile for profile in self.profiles if profile.id == target), None)
        if profile is None:
            raise ValueError("请先配置并选择模型档案")
        return profile

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, root: Path = DEFAULT_ROOT) -> HarnessConfig:
        value = dict(raw)
        value.pop("root", None)
        value["profiles"] = tuple(ModelProfile.from_dict(item) for item in value.get("profiles", ()))
        value["group_ids"] = tuple(int(item) for item in value.get("group_ids", ()))
        names = cls.__dataclass_fields__
        extra = dict(value.pop("extra", {}))
        extra.update({key: item for key, item in value.items() if key not in names})
        result = cls(root=Path(root).resolve(), extra=extra,
                     **{key: item for key, item in value.items() if key in names})
        if result.mode not in {"observe", "replay", "live"}:
            raise ValueError("mode 必须是 observe、replay 或 live")
        if not (0 < result.soft_budget_ratio < result.hard_budget_ratio <= 1):
            raise ValueError("上下文水位必须满足 0 < soft < hard <= 1")
        if result.input_budget_tokens <= 0 or result.recent_rounds < 1:
            raise ValueError("上下文预算和保留轮次必须大于零")
        _validate_cache_budget(result.cache_input_budget_tokens)
        if result.extra.get('context_mode', 'cache_first') not in {'cache_first', 'legacy'}:
            raise ValueError('context_mode 必须是 cache_first 或 legacy')
        return result


def _validate_cache_budget(value: int | None) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise ValueError('cache_input_budget_tokens 必须为 null（自动）或正整数')


def effective_cache_input_budget(config: HarnessConfig, profile: ModelProfile,
                                 override: int | None = None) -> int | None:
    """Derive the input limit from configured model capacity, never infer a capacity."""
    configured = config.cache_input_budget_tokens if override is None else override
    _validate_cache_budget(configured)
    if profile.context_limit is None:
        return None
    capacity = profile.context_limit - profile.max_output_tokens - 1024
    if capacity <= 0:
        raise ValueError('模型上下文容量必须大于输出预算加 1024 token')
    return min(configured, capacity) if configured is not None else capacity


def config_path(root: Path = DEFAULT_ROOT) -> Path:
    return Path(root) / "config" / "settings.json"


def load_config(root: Path = DEFAULT_ROOT) -> HarnessConfig:
    path = config_path(root)
    if not path.exists():
        return HarnessConfig(root=Path(root).resolve())
    return HarnessConfig.from_dict(json.loads(path.read_text(encoding="utf-8")), root=root)


def save_config(config: HarnessConfig) -> None:
    path = config_path(config.root)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = asdict(config)
    raw.pop("root")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def public_config(config: HarnessConfig) -> dict[str, Any]:
    result = asdict(config)
    result.pop("root", None)
    result["onebot_access_token_configured"] = bool(result.pop("onebot_access_token", ""))
    for profile in result["profiles"]:
        profile["api_key_configured"] = bool(profile.pop("api_key", "") or profile.get('api_key_env'))
        profile["extra_body"] = redact_payload(profile.get("extra_body", {}))
    result["extra"] = _redact_extra(result.get("extra", {}))
    cache_first = config.extra.get('context_mode', 'cache_first') == 'cache_first'
    profile = config.active_profile
    configured = config.cache_input_budget_tokens if cache_first else config.input_budget_tokens
    capacity = profile.context_limit if profile else None
    usable = capacity - profile.max_output_tokens - 1024 if capacity is not None else None
    effective = (effective_cache_input_budget(config, profile) if cache_first
                 else min(config.input_budget_tokens, usable)) if profile and usable is not None else None
    source = ('unknown_capacity' if effective is None else
              'model_capacity' if cache_first and configured is None else 'configured_limit')
    result['context_policy'] = {
        'mode': 'cache_first' if cache_first else 'legacy',
        'input_budget_tokens': effective,
        'configured_input_budget_tokens': configured,
        'effective_input_budget_tokens': effective,
        'budget_source': source,
        'model_context_limit': capacity,
        'capacity_source': 'profile_configuration' if capacity is not None else 'unknown',
        'capacity_verification': 'not_verified_by_harness',
        'reserved_output_tokens': profile.max_output_tokens if profile else None,
        'safety_reserve_tokens': 1024,
        'rebuild_trigger_ratio': config.hard_budget_ratio,
        'rebuild_trigger_tokens': int(effective * config.hard_budget_ratio) if effective is not None else None,
        'rebuild_target_ratio': 0.5,
        'rebuild_target_tokens': int(effective * 0.5) if effective is not None else None,
        'automatic_learning': not cache_first,
        'automatic_summaries': not cache_first,
        'cross_model_fallback': not cache_first,
    }
    return result


def _redact_extra(value: Any) -> Any:
    return redact_payload(value)
