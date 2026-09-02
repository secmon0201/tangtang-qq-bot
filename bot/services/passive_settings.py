from __future__ import annotations

from dataclasses import dataclass, replace

from bot.config import settings
from bot.db import Database
from bot.services.env_sync import (
    sync_feature_scope,
    sync_game_api_enabled,
    sync_passive_group_value,
)


REACTION_PROBABILITY_KEY = "reaction_probability"
REACTION_COOLDOWN_KEY = "reaction_cooldown_seconds"
REPEAT_PROBABILITY_KEY = "repeat_probability"
REPEAT_COOLDOWN_KEY = "repeat_cooldown_seconds"
REPEAT_INTERVAL_KEY = "repeat_message_interval"
TRIPLE_REPEAT_ENABLED_KEY = "triple_repeat_enabled"
TRIPLE_REPEAT_PROBABILITY_KEY = "triple_repeat_probability"
PASSIVE_GROUP_IDS_KEY = "passive_group_ids"
HOURLY_ANNOUNCEMENT_GROUP_IDS_KEY = "hourly_announcement_group_ids"
GAME_MUTE_GROUP_IDS_KEY = "game_mute_group_ids"
GAME_MUTE_DISABLED_GROUP_IDS_KEY = "game_mute_disabled_group_ids"
GAME_GLOBAL_ENABLED_KEY = "game_global_enabled"
GAME_API_GROUP_IDS_KEY = "game_api_group_ids"
GAME_API_ENABLED_KEY = "game_api_enabled"
CHAT_GLOBAL_ENABLED_KEYS = {
    "mention_chat": "mention_chat_global_enabled",
    "proactive_chat": "proactive_chat_global_enabled",
}
FEATURE_SCOPE_KEYS = {
    "duplicate": "duplicate_group_ids",
    "game": "game_group_ids",
    "game_api": GAME_API_GROUP_IDS_KEY,
    "today_wife": "today_wife_group_ids",
    "passive": PASSIVE_GROUP_IDS_KEY,
    "hourly": HOURLY_ANNOUNCEMENT_GROUP_IDS_KEY,
    "game_mute": GAME_MUTE_GROUP_IDS_KEY,
    "game_mute_disabled": GAME_MUTE_DISABLED_GROUP_IDS_KEY,
}
FEATURE_DOMAIN_KEYS = {
    "duplicate": "duplicate",
    "game": "mini_games",
    "game_api": "nte",
    "today_wife": "today_wife",
    "passive": "passive_interaction",
    "hourly": "hourly",
}


@dataclass(frozen=True, slots=True)
class PassiveSettings:
    reaction_probability: float
    reaction_cooldown_seconds: int
    repeat_probability: float
    repeat_cooldown_seconds: int
    repeat_message_interval: int
    triple_repeat_enabled: bool = False
    triple_repeat_probability: float = 0.30


class PassiveSettingsStore:
    """Persist per-group passive settings while keeping event lookups in-memory."""

    def __init__(
        self,
        database: Database,
        defaults: PassiveSettings | None = None,
        *,
        sync_env: bool = False,
    ) -> None:
        self.database = database
        self.sync_env = sync_env
        self.defaults = defaults or PassiveSettings(
            reaction_probability=settings.random_reaction_probability,
            reaction_cooldown_seconds=settings.random_reaction_cooldown_seconds,
            repeat_probability=settings.random_repeat_probability,
            repeat_cooldown_seconds=settings.random_repeat_cooldown_seconds,
            repeat_message_interval=settings.random_repeat_message_interval,
            triple_repeat_probability=settings.random_triple_repeat_probability,
        )
        self._env_defaults_by_group: dict[int, PassiveSettings] = {}
        values = self.database.passive_settings()
        # Existing global values become the initial template for every group.
        # Per-group rows override this template and are never shared across groups.
        self.current = PassiveSettings(
            reaction_probability=self._float(values, REACTION_PROBABILITY_KEY, self.defaults.reaction_probability),
            reaction_cooldown_seconds=self._integer(
                values, REACTION_COOLDOWN_KEY, self.defaults.reaction_cooldown_seconds
            ),
            repeat_probability=self._float(values, REPEAT_PROBABILITY_KEY, self.defaults.repeat_probability),
            repeat_cooldown_seconds=self._integer(
                values, REPEAT_COOLDOWN_KEY, self.defaults.repeat_cooldown_seconds
            ),
            repeat_message_interval=self._integer(
                values, REPEAT_INTERVAL_KEY, self.defaults.repeat_message_interval
            ),
            triple_repeat_enabled=self._boolean(
                values, TRIPLE_REPEAT_ENABLED_KEY, self.defaults.triple_repeat_enabled
            ),
            triple_repeat_probability=self._float(
                values,
                TRIPLE_REPEAT_PROBABILITY_KEY,
                self.defaults.triple_repeat_probability,
            ),
        )
        self._groups = {
            feature: self._group_ids(values, feature)
            for feature in FEATURE_SCOPE_KEYS
        }
        self._game_globally_enabled = self._boolean(values, GAME_GLOBAL_ENABLED_KEY, True)
        if GAME_GLOBAL_ENABLED_KEY not in values:
            self.database.set_passive_setting(GAME_GLOBAL_ENABLED_KEY, "true")
        self._game_api_enabled = self._boolean(
            values, GAME_API_ENABLED_KEY, settings.game_api_enabled
        )
        if GAME_API_ENABLED_KEY not in values:
            self.database.set_passive_setting(
                GAME_API_ENABLED_KEY, str(self._game_api_enabled).lower()
            )
        self._chat_globally_enabled = {
            feature_key: self._boolean(values, setting_key, True)
            for feature_key, setting_key in CHAT_GLOBAL_ENABLED_KEYS.items()
        }
        for feature_key, setting_key in CHAT_GLOBAL_ENABLED_KEYS.items():
            if setting_key not in values:
                self.database.set_passive_setting(
                    setting_key,
                    str(self._chat_globally_enabled[feature_key]).lower(),
                )
        if self.sync_env:
            sync_game_api_enabled(self._game_api_enabled)
        self._settings_by_group = {
            group_id: self._load_group_settings(group_id)
            for group_id in self.group_ids
        }
        if self.sync_env and defaults is None:
            for group_id in self.group_ids:
                self._sync_repeat_defaults_from_env(group_id)

    @staticmethod
    def _float(values: dict[str, str], key: str, default: float) -> float:
        try:
            return float(values.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _integer(values: dict[str, str], key: str, default: int) -> int:
        try:
            return int(values.get(key, default))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _boolean(values: dict[str, str], key: str, default: bool) -> bool:
        value = str(values.get(key, default)).strip().lower()
        if value in {"1", "true", "yes", "on", "开启"}:
            return True
        if value in {"0", "false", "no", "off", "关闭"}:
            return False
        return default

    @staticmethod
    def _csv_group_ids(value: str) -> frozenset[int]:
        ids: set[int] = set()
        for item in value.split(","):
            item = item.strip()
            if item.isdigit() and int(item) > 0:
                ids.add(int(item))
        return frozenset(ids)

    def _group_ids(self, values: dict[str, str], feature: str) -> frozenset[int]:
        raw = values.get(FEATURE_SCOPE_KEYS[feature])
        defaults = {
            "duplicate": settings.duplicate_group_ids,
            "game": settings.game_group_ids or settings.managed_group_ids,
            "game_api": settings.game_api_group_ids or settings.managed_group_ids,
            "today_wife": settings.managed_group_ids,
            "passive": settings.random_reaction_group_ids,
            "hourly": settings.hourly_announcement_group_ids,
            "game_mute": (),
            "game_mute_disabled": (),
        }
        selected = (
            self._csv_group_ids(raw)
            if raw is not None
            else frozenset(defaults[feature])
        )
        return frozenset(group_id for group_id in selected if group_id in settings.managed_group_ids)

    @property
    def group_ids(self) -> frozenset[int]:
        return self.groups("passive")

    def groups(self, feature: str) -> frozenset[int]:
        if feature in FEATURE_DOMAIN_KEYS:
            selected = self.database.enabled_feature_groups(FEATURE_DOMAIN_KEYS[feature])
            if selected or self.database.managed_groups():
                return selected
        return self._groups[feature]

    def is_feature_group_enabled(self, feature: str, group_id: int) -> bool:
        return int(group_id) in self.groups(feature)

    def is_group_enabled(self, group_id: int) -> bool:
        return self.is_feature_group_enabled("passive", group_id)

    def is_game_mute_enabled(self, group_id: int) -> bool:
        """Game punishment mutes default to on unless a super admin disables one group."""
        return self.is_feature_group_enabled("game", group_id) and not self.is_feature_group_enabled(
            "game_mute_disabled", group_id
        )

    def is_game_globally_enabled(self) -> bool:
        return self._game_globally_enabled

    def set_game_globally_enabled(self, enabled: bool) -> bool:
        self._game_globally_enabled = bool(enabled)
        self.database.set_passive_setting(GAME_GLOBAL_ENABLED_KEY, str(bool(enabled)).lower())
        return self._game_globally_enabled

    def is_game_api_enabled(self) -> bool:
        """Hot switch for the independent game interface (NTEUID via #nte)."""
        return self._game_api_enabled

    def set_game_api_enabled(self, enabled: bool) -> bool:
        self._game_api_enabled = bool(enabled)
        self.database.set_passive_setting(GAME_API_ENABLED_KEY, str(bool(enabled)).lower())
        if self.sync_env:
            sync_game_api_enabled(self._game_api_enabled)
        return self._game_api_enabled

    def is_chat_globally_enabled(self, feature_key: str) -> bool:
        if feature_key not in CHAT_GLOBAL_ENABLED_KEYS:
            raise ValueError("unsupported chat feature")
        return self._chat_globally_enabled[feature_key]

    def set_chat_globally_enabled(self, feature_key: str, enabled: bool) -> bool:
        setting_key = CHAT_GLOBAL_ENABLED_KEYS.get(feature_key)
        if setting_key is None:
            raise ValueError("unsupported chat feature")
        self._chat_globally_enabled[feature_key] = bool(enabled)
        self.database.set_passive_setting(setting_key, str(bool(enabled)).lower())
        return self._chat_globally_enabled[feature_key]

    def for_group(self, group_id: int) -> PassiveSettings:
        group_id = int(group_id)
        if not self.database.is_managed_group(group_id):
            raise ValueError("group is outside the managed scope")
        if group_id not in self._settings_by_group:
            self._settings_by_group[group_id] = self._load_group_settings(group_id)
        return self._settings_by_group[group_id]

    def group_settings(self) -> tuple[tuple[int, PassiveSettings], ...]:
        return tuple((group_id, self.for_group(group_id)) for group_id in sorted(self.group_ids))

    def add_group(self, group_id: int) -> frozenset[int]:
        return self.add_feature_group("passive", group_id)

    def add_feature_group(self, feature: str, group_id: int) -> frozenset[int]:
        if feature not in FEATURE_SCOPE_KEYS:
            raise ValueError("unsupported feature scope")
        group_id = int(group_id)
        if not self.database.is_managed_group(group_id):
            if group_id not in settings.managed_group_ids:
                raise ValueError("group is outside the managed scope")
            self.database.ensure_group(group_id)
            for legacy_feature, canonical_feature in FEATURE_DOMAIN_KEYS.items():
                self.database.set_group_feature(
                    group_id,
                    canonical_feature,
                    group_id in self._groups[legacy_feature],
                )
        if feature in FEATURE_DOMAIN_KEYS:
            self.database.set_group_feature(group_id, FEATURE_DOMAIN_KEYS[feature], True)
            if feature == "passive":
                self._settings_by_group.setdefault(group_id, self._load_group_settings(group_id))
            return self.groups(feature)
        self._groups[feature] = frozenset((*self.groups(feature), group_id))
        self._persist_groups(feature)
        if feature == "passive":
            self._settings_by_group.setdefault(group_id, self._load_group_settings(group_id))
        return self.groups(feature)

    def remove_group(self, group_id: int) -> frozenset[int]:
        return self.remove_feature_group("passive", group_id)

    def remove_feature_group(self, feature: str, group_id: int) -> frozenset[int]:
        if feature not in FEATURE_SCOPE_KEYS:
            raise ValueError("unsupported feature scope")
        if feature in FEATURE_DOMAIN_KEYS:
            self.database.set_group_feature(
                int(group_id), FEATURE_DOMAIN_KEYS[feature], False
            )
            return self.groups(feature)
        self._groups[feature] = frozenset(
            item for item in self.groups(feature) if item != int(group_id)
        )
        self._persist_groups(feature)
        return self.groups(feature)

    def _persist_groups(self, feature: str) -> None:
        self.database.set_passive_setting(
            FEATURE_SCOPE_KEYS[feature],
            ",".join(str(group_id) for group_id in sorted(self.groups(feature))),
        )
        if self.sync_env:
            sync_feature_scope(feature, self.groups(feature))

    def _load_group_settings(self, group_id: int) -> PassiveSettings:
        values = self.database.passive_group_settings(group_id)
        group_settings = PassiveSettings(
            reaction_probability=self._float(values, REACTION_PROBABILITY_KEY, self.current.reaction_probability),
            reaction_cooldown_seconds=self._integer(
                values, REACTION_COOLDOWN_KEY, self.current.reaction_cooldown_seconds
            ),
            repeat_probability=self._float(values, REPEAT_PROBABILITY_KEY, self.current.repeat_probability),
            repeat_cooldown_seconds=self._integer(
                values, REPEAT_COOLDOWN_KEY, self.current.repeat_cooldown_seconds
            ),
            repeat_message_interval=self._integer(
                values, REPEAT_INTERVAL_KEY, self.current.repeat_message_interval
            ),
            triple_repeat_enabled=self._boolean(
                values, TRIPLE_REPEAT_ENABLED_KEY, self.current.triple_repeat_enabled
            ),
            triple_repeat_probability=self._float(
                values,
                TRIPLE_REPEAT_PROBABILITY_KEY,
                self.current.triple_repeat_probability,
            ),
        )
        if not values:
            # Materialize the former shared setting once so future groups are
            # fully independent even if a legacy template is later changed.
            self._persist_group_settings(group_id, group_settings)
        return group_settings

    def _sync_repeat_defaults_from_env(self, group_id: int) -> None:
        """Make .env the runtime source for repeat settings without touching reactions."""

        group_id = int(group_id)
        if group_id not in settings.random_reaction_group_ids:
            return
        current = self._settings_by_group[group_id]
        updated = replace(
            current,
            repeat_probability=settings.random_repeat_probability_by_group[group_id],
            repeat_cooldown_seconds=settings.random_repeat_cooldown_seconds_by_group[group_id],
            repeat_message_interval=settings.random_repeat_message_interval_by_group[group_id],
            triple_repeat_enabled=settings.random_triple_repeat_enabled_by_group[group_id],
            triple_repeat_probability=settings.random_triple_repeat_probability_by_group[group_id],
        )
        self._persist_group_settings(group_id, updated)
        self._settings_by_group[group_id] = updated

    def _persist_group_settings(self, group_id: int, group_settings: PassiveSettings) -> None:
        values = {
            REACTION_PROBABILITY_KEY: group_settings.reaction_probability,
            REACTION_COOLDOWN_KEY: group_settings.reaction_cooldown_seconds,
            REPEAT_PROBABILITY_KEY: group_settings.repeat_probability,
            REPEAT_COOLDOWN_KEY: group_settings.repeat_cooldown_seconds,
            REPEAT_INTERVAL_KEY: group_settings.repeat_message_interval,
            TRIPLE_REPEAT_ENABLED_KEY: str(group_settings.triple_repeat_enabled).lower(),
            TRIPLE_REPEAT_PROBABILITY_KEY: group_settings.triple_repeat_probability,
        }
        for key, value in values.items():
            self.database.set_passive_group_setting(group_id, key, str(value))

    def _replace_group(self, group_id: int, key: str, value: str, **changes: object) -> PassiveSettings:
        current = self.for_group(group_id)
        updated = replace(current, **changes)
        self.database.set_passive_group_setting(group_id, key, value)
        self._settings_by_group[int(group_id)] = updated
        if self.sync_env and key in {
            REPEAT_PROBABILITY_KEY,
            REPEAT_COOLDOWN_KEY,
            REPEAT_INTERVAL_KEY,
            TRIPLE_REPEAT_ENABLED_KEY,
            TRIPLE_REPEAT_PROBABILITY_KEY,
        }:
            sync_passive_group_value(key, group_id, value)
        return updated

    def set_reaction_probability(self, group_id: int, value: float) -> PassiveSettings:
        return self._replace_group(
            group_id, REACTION_PROBABILITY_KEY, str(value), reaction_probability=float(value)
        )

    def set_reaction_cooldown_seconds(self, group_id: int, value: int) -> PassiveSettings:
        return self._replace_group(
            group_id, REACTION_COOLDOWN_KEY, str(value), reaction_cooldown_seconds=int(value)
        )

    def set_repeat_probability(self, group_id: int, value: float) -> PassiveSettings:
        return self._replace_group(
            group_id, REPEAT_PROBABILITY_KEY, str(value), repeat_probability=float(value)
        )

    def set_repeat_cooldown_seconds(self, group_id: int, value: int) -> PassiveSettings:
        return self._replace_group(
            group_id, REPEAT_COOLDOWN_KEY, str(value), repeat_cooldown_seconds=int(value)
        )

    def set_repeat_message_interval(self, group_id: int, value: int) -> PassiveSettings:
        return self._replace_group(
            group_id, REPEAT_INTERVAL_KEY, str(value), repeat_message_interval=int(value)
        )

    def set_triple_repeat_enabled(self, group_id: int, enabled: bool) -> PassiveSettings:
        return self._replace_group(
            group_id,
            TRIPLE_REPEAT_ENABLED_KEY,
            str(bool(enabled)).lower(),
            triple_repeat_enabled=bool(enabled),
        )

    def set_triple_repeat_probability(self, group_id: int, value: float) -> PassiveSettings:
        return self._replace_group(
            group_id,
            TRIPLE_REPEAT_PROBABILITY_KEY,
            str(float(value)),
            triple_repeat_probability=float(value),
        )
