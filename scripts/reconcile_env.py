from __future__ import annotations

from bot.services.env_sync import (
    FEATURE_SCOPE_ENV_KEYS,
    TEMPLATE_ENV_KEYS,
    read_env_value,
    update_env_value,
)
from bot.services.runtime import passive_settings


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def main() -> int:
    passive = passive_settings()
    updated: list[str] = []

    for feature, env_key in sorted(FEATURE_SCOPE_ENV_KEYS.items()):
        value = ",".join(
            str(int(group_id)) for group_id in sorted(passive.groups(feature))
        )
        if read_env_value(env_key) != value:
            update_env_value(env_key, value)
            updated.append(env_key)

    for setting_name, env_key in sorted(TEMPLATE_ENV_KEYS.items()):
        value = _fmt(getattr(passive.current, setting_name))
        if read_env_value(env_key) != value:
            update_env_value(env_key, value)
            updated.append(env_key)

    if updated:
        print("env reconciled (keys):", ", ".join(updated))
    else:
        print("env already matches runtime values")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
