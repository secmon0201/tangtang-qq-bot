from __future__ import annotations

from enum import StrEnum

from bot.config import settings


class UserRole(StrEnum):
    USER = "user"
    ACTIVITY_ADMIN = "activity_admin"
    SUPER_ADMIN = "super_admin"


def is_activity_admin_blacklisted(user_id: int) -> bool:
    return int(user_id) in getattr(settings, "activity_admin_blacklist_ids", frozenset())


def user_role(user_id: int) -> UserRole:
    value = int(user_id)
    if value in settings.operator_ids:
        return UserRole.SUPER_ADMIN
    if is_activity_admin_blacklisted(value):
        return UserRole.USER
    if value in settings.activity_admin_ids:
        return UserRole.ACTIVITY_ADMIN
    return UserRole.USER


def is_super_admin(user_id: int) -> bool:
    return user_role(user_id) is UserRole.SUPER_ADMIN


def is_global_announcement_operator(user_id: int) -> bool:
    value = int(user_id)
    return is_super_admin(value) or value in getattr(
        settings, "global_announcement_operator_ids", frozenset()
    )


def is_activity_admin(user_id: int) -> bool:
    return user_role(user_id) in {UserRole.ACTIVITY_ADMIN, UserRole.SUPER_ADMIN}


def role_label(user_id: int) -> str:
    return {
        UserRole.USER: "普通用户",
        UserRole.ACTIVITY_ADMIN: "活动管理员",
        UserRole.SUPER_ADMIN: "超级管理员",
    }[user_role(user_id)]
