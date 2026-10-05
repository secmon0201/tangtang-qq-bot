from __future__ import annotations

from pathlib import Path

import pytest

from bot.services import tangtang_models
from bot.services.tangtang_chat import TangtangConfigLoader
from scripts.probe_tangtang_vision import _payload


def _profiled_env(active: str = "模型1") -> str:
    return (
        "TANGTANG_ENABLED=true\n"
        "TANGTANG_GROUP_IDS=1001\n"
        "TANGTANG_VISION_DETAIL=auto\n"
        f"TANGTANG_MODEL_ACTIVE_PROFILE={active}\n"
        "TANGTANG_MODEL_PROFILE_1_NAME=模型1\n"
        "TANGTANG_MODEL_PROFILE_1_PROVIDER=deepseek\n"
        "TANGTANG_MODEL_PROFILE_1_API_URL=https://proxy.example.invalid/v1\n"
        "TANGTANG_MODEL_PROFILE_1_API_KEY=test-only\n"
        "TANGTANG_MODEL_PROFILE_1_API_STYLE=chat_completions\n"
        "TANGTANG_MODEL_PROFILE_1_MODEL=cline-pass/deepseek-v4.1-flash\n"
        "TANGTANG_MODEL_PROFILE_1_REASONING_EFFORT=max\n"
        "TANGTANG_MODEL_PROFILE_1_VISION_DETAIL=high\n"
        "TANGTANG_MODEL_PROFILE_3_NAME=模型3\n"
        "TANGTANG_MODEL_PROFILE_3_PROVIDER=deepseek\n"
        "TANGTANG_MODEL_PROFILE_3_API_URL=https://api.deepseek.com\n"
        "TANGTANG_MODEL_PROFILE_3_API_KEY=other-test-only\n"
        "TANGTANG_MODEL_PROFILE_3_API_STYLE=responses\n"
        "TANGTANG_MODEL_PROFILE_3_MODEL=deepseek-flash\n"
        "TANGTANG_MODEL_PROFILE_3_REASONING_EFFORT=low\n"
        "TANGTANG_MODEL_PROFILE_3_VISION_DETAIL=low\n"
    )


def test_loader_uses_the_active_numbered_model_profile(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_profiled_env("模型1"), encoding="utf-8")

    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))
    config = loader.load()

    assert config.enabled is True
    assert config.provider == "deepseek"
    assert config.api_url == "https://proxy.example.invalid/v1"
    assert config.api_key == "test-only"
    assert config.api_style == "chat_completions"
    assert config.model == "cline-pass/deepseek-v4.1-flash"
    assert config.reasoning_effort == "max"
    assert config.vision_detail == "high"
    assert [profile.name for profile in loader.model_profiles()] == ["模型1", "模型3"]


def test_loader_switches_to_any_configured_profile_and_persists_it(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_profiled_env("模型3"), encoding="utf-8")
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))

    config = loader.activate_model_profile("模型1")

    assert config.model == "cline-pass/deepseek-v4.1-flash"
    assert config.reasoning_effort == "max"
    assert config.vision_detail == "high"
    assert "TANGTANG_MODEL_ACTIVE_PROFILE=模型1" in env_path.read_text(encoding="utf-8")


def test_loader_keeps_high_visual_detail_when_switching_reasoning_profile(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_profiled_env("模型1"), encoding="utf-8")
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))

    config = loader.activate_model_profile("模型3")

    assert config.model == "deepseek-flash"
    assert config.reasoning_effort == "low"
    assert config.vision_detail == "high"


def test_loader_rejects_unknown_profile_without_changing_env(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_profiled_env("模型1"), encoding="utf-8")
    original = env_path.read_text(encoding="utf-8")
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))

    with pytest.raises(ValueError, match="unknown Tangtang model profile"):
        loader.activate_model_profile("模型9")

    assert env_path.read_text(encoding="utf-8") == original


def test_loader_rejects_duplicate_profile_names(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        _profiled_env("模型1").replace(
            "TANGTANG_MODEL_PROFILE_3_NAME=模型3",
            "TANGTANG_MODEL_PROFILE_3_NAME=模型1",
        ),
        encoding="utf-8",
    )
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))

    with pytest.raises(ValueError, match="duplicate Tangtang model profile name"):
        loader.model_profiles()


def test_loader_keeps_legacy_single_model_configuration(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TANGTANG_ENABLED=true\n"
        "TANGTANG_GROUP_IDS=1001\n"
        "TANGTANG_API_URL=https://api.deepseek.com\n"
        "TANGTANG_API_KEY=test-only\n"
        "TANGTANG_API_STYLE=responses\n"
        "TANGTANG_MODEL=deepseek-flash\n"
        "TANGTANG_REASONING_EFFORT=low\n",
        encoding="utf-8",
    )

    config = TangtangConfigLoader(env_path, managed_group_ids=(1001,)).load()

    assert config.enabled is True
    assert config.model == "deepseek-flash"


def test_model_status_lists_profiles_without_credentials(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(_profiled_env("模型1"), encoding="utf-8")
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))

    text = tangtang_models.model_profile_status(loader.model_profile_catalog())

    assert "模型1（当前）" in text
    assert "模型3" in text
    assert "cline-pass/deepseek-v4.1-flash" in text
    assert "deepseek-flash" in text
    assert "test-only" not in text
    assert "other-test-only" not in text
    assert "proxy.example.invalid" not in text


@pytest.mark.parametrize("api_style", ("responses", "chat_completions"))
def test_vision_probe_does_not_require_unrelated_tool_support(api_style: str):
    payload = _payload("test-model", api_style, "none", "high")

    assert "tools" not in payload
    token_key = (
        "max_output_tokens" if api_style == "responses" else "max_completion_tokens"
    )
    assert payload[token_key] >= 512
    if api_style == "responses":
        image_part = payload["input"][0]["content"][1]
        assert image_part["detail"] == "high"
    else:
        image_part = payload["messages"][0]["content"][1]
        assert image_part["image_url"]["detail"] == "high"
