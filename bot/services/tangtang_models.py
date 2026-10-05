from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from bot.services.env_sync import update_env_value


ACTIVE_PROFILE_KEY = "TANGTANG_MODEL_ACTIVE_PROFILE"
_PROFILE_NAME_RE = re.compile(r"^TANGTANG_MODEL_PROFILE_(\d+)_NAME$")
_API_STYLES = frozenset({"responses", "chat_completions"})
_REASONING_EFFORTS = frozenset({"none", "low", "high", "max"})
VISION_DETAIL_LEVELS = frozenset({"auto", "low", "high", "original"})


def _raw(values: Mapping[str, Any], key: str, default: str = "") -> str:
    value = values.get(key)
    return default if value is None else str(value).strip()


@dataclass(frozen=True, slots=True)
class TangtangModelProfile:
    name: str
    provider: str
    api_url: str
    api_key: str
    api_style: str
    model: str
    reasoning_effort: str
    vision_detail: str | None = None

    def config_values(self) -> dict[str, str]:
        values = {
            "TANGTANG_PROVIDER": self.provider,
            "TANGTANG_API_URL": self.api_url,
            "TANGTANG_API_KEY": self.api_key,
            "TANGTANG_API_STYLE": self.api_style,
            "TANGTANG_MODEL": self.model,
            "TANGTANG_REASONING_EFFORT": self.reasoning_effort,
        }
        if self.vision_detail is not None:
            values["TANGTANG_VISION_DETAIL"] = self.vision_detail
        return values


@dataclass(frozen=True, slots=True)
class TangtangModelCatalog:
    profiles: tuple[TangtangModelProfile, ...]
    active_name: str

    def profile(self, name: str) -> TangtangModelProfile:
        target = str(name).strip().casefold()
        for profile in self.profiles:
            if profile.name.casefold() == target:
                return profile
        raise ValueError(f"unknown Tangtang model profile: {name}")

    @property
    def active(self) -> TangtangModelProfile:
        return self.profile(self.active_name)


def model_profile_status(
    catalog: TangtangModelCatalog,
    title: str = "糖糖模型当前配置",
) -> str:
    lines = [title]
    for profile in catalog.profiles:
        current = "（当前）" if profile.name == catalog.active.name else ""
        lines.append(
            f"{profile.name}{current}："
            f"{profile.provider} / {profile.model} / {profile.api_style}"
        )
    lines.append("切换：#糖糖模型 <档案名>")
    return "\n".join(lines)


def model_catalog(values: Mapping[str, Any]) -> TangtangModelCatalog | None:
    slots = sorted(
        {
            int(match.group(1))
            for key in values
            if (match := _PROFILE_NAME_RE.fullmatch(str(key)))
            and _raw(values, str(key))
        }
    )
    if not slots:
        return None

    profiles: list[TangtangModelProfile] = []
    names: set[str] = set()
    for slot in slots:
        prefix = f"TANGTANG_MODEL_PROFILE_{slot}_"
        name = _raw(values, prefix + "NAME")
        folded_name = name.casefold()
        if folded_name in names:
            raise ValueError(f"duplicate Tangtang model profile name: {name}")
        names.add(folded_name)

        provider = _raw(values, prefix + "PROVIDER", "custom")
        api_url = _raw(values, prefix + "API_URL")
        api_key = _raw(values, prefix + "API_KEY")
        model = _raw(values, prefix + "MODEL")
        if not api_url or not api_key or not model:
            raise ValueError(
                f"Tangtang model profile {name} requires API_URL, API_KEY and MODEL"
            )
        api_style = _raw(values, prefix + "API_STYLE", "responses").lower()
        if api_style not in _API_STYLES:
            raise ValueError(f"Tangtang model profile {name} has invalid API_STYLE")
        reasoning_effort = _raw(
            values, prefix + "REASONING_EFFORT", "none"
        ).lower()
        if reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError(
                f"Tangtang model profile {name} has invalid REASONING_EFFORT"
            )
        vision_detail = _raw(values, prefix + "VISION_DETAIL").lower() or None
        if vision_detail is not None and vision_detail not in VISION_DETAIL_LEVELS:
            raise ValueError(
                f"Tangtang model profile {name} has invalid VISION_DETAIL"
            )
        profiles.append(
            TangtangModelProfile(
                name=name,
                provider=provider,
                api_url=api_url,
                api_key=api_key,
                api_style=api_style,
                model=model,
                reasoning_effort=reasoning_effort,
                vision_detail=vision_detail,
            )
        )

    active_name = _raw(values, ACTIVE_PROFILE_KEY, profiles[0].name)
    catalog = TangtangModelCatalog(tuple(profiles), active_name)
    catalog.active
    return catalog


def resolve_model_profile(values: Mapping[str, Any]) -> Mapping[str, Any]:
    catalog = model_catalog(values)
    if catalog is None:
        return values
    resolved = dict(values)
    resolved.update(catalog.active.config_values())
    return resolved


def activate_model_profile(path: Path, name: str) -> None:
    catalog = model_catalog(dotenv_values(path))
    if catalog is None:
        raise ValueError("no Tangtang model profiles are configured")
    selected = catalog.profile(name)
    update_env_value(ACTIVE_PROFILE_KEY, selected.name, path=path)


__all__ = [
    "ACTIVE_PROFILE_KEY",
    "TangtangModelCatalog",
    "TangtangModelProfile",
    "VISION_DETAIL_LEVELS",
    "activate_model_profile",
    "model_catalog",
    "model_profile_status",
    "resolve_model_profile",
]
