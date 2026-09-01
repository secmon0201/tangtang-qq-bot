from __future__ import annotations

from enum import StrEnum

from bot.config import settings


class UserRole(StrEnum):
    USER = "user"
    SUPER_ADMIN = "super_admin"


def user_role(user_id: int) -> UserRole:
    value = int(user_id)
    if value in settings.operator_ids:
        return UserRole.SUPER_ADMIN
    return UserRole.USER


def is_super_admin(user_id: int) -> bool:
    return user_role(user_id) is UserRole.SUPER_ADMIN


def is_global_announcement_operator(user_id: int) -> bool:
    value = int(user_id)
    return is_super_admin(value) or value in getattr(
        settings, "global_announcement_operator_ids", frozenset()
    )


def role_label(user_id: int) -> str:
    return {
        UserRole.USER: "普通用户",
        UserRole.SUPER_ADMIN: "超级管理员",
    }[user_role(user_id)]
