from __future__ import annotations

from functools import lru_cache

from bot.config import A_COAST_GROUP_IDS, settings
from bot.db import Database
from bot.services.group_domains import GroupDomainService
from bot.services.passive_settings import PassiveSettingsStore


@lru_cache(maxsize=1)
def database() -> Database:
    instance = Database(settings.db_path)
    instance.seed_groups((*settings.managed_group_ids, *A_COAST_GROUP_IDS))
    return instance


@lru_cache(maxsize=1)
def group_domains() -> GroupDomainService:
    instance = GroupDomainService(database())
    instance.bootstrap(
        legacy_feature_groups={
            "duplicate": settings.duplicate_group_ids,
            "mini_games": settings.game_group_ids or settings.managed_group_ids,
            "nte": settings.game_api_group_ids or settings.managed_group_ids,
            "ww": settings.game_api_group_ids or settings.managed_group_ids,
            "today_wife": settings.managed_group_ids,
            "passive_interaction": settings.random_reaction_group_ids,
            "hourly": settings.hourly_announcement_group_ids,
            "bilibili": settings.asoul_bili_group_ids,
            "zhijiang_calendar": settings.managed_group_ids,
        }
    )
    return instance


@lru_cache(maxsize=1)
def passive_settings() -> PassiveSettingsStore:
    group_domains()
    return PassiveSettingsStore(database(), sync_env=False)


@lru_cache(maxsize=1)
def zhijiang_live_guard():
    """Share one schedule cache and live-pause state across all plugins."""
    from bot.services.zhijiang_live_guard import ZhijiangLiveGuard

    return ZhijiangLiveGuard(
        database(),
        passive_settings(),
        enabled=settings.zhijiang_live_guard_enabled,
        source_url=settings.zhijiang_schedule_url,
        timezone=settings.timezone,
        refresh_minutes=settings.zhijiang_schedule_refresh_minutes,
        pause_minutes=settings.zhijiang_live_pause_minutes,
        lookahead_days=settings.zhijiang_schedule_lookahead_days,
        timeout_seconds=settings.zhijiang_schedule_timeout,
    )
