"""Pure command-shape classifiers shared by transport plugins."""

from __future__ import annotations

import re


ACTIVITY_COMMAND_RE = re.compile(
    r"^(?:活动管理员帮助|查看获奖名单|活动帮助|活动大厅|活动详情|查看名单|获奖名单|我的活动|"
    r"创建活动|修改活动|取消活动|提前结束|取消报名|报名)(?=$|[\s\u3000:：#0-9])"
)
PARTICIPATION_COMMAND_RE = re.compile(r"^(?:报名|取消报名)(?=$|[\s\u3000:：#0-9])")


def is_activity_command_text(text: str) -> bool:
    return bool(ACTIVITY_COMMAND_RE.match(text.strip()))


def is_participation_command_text(text: str) -> bool:
    return bool(PARTICIPATION_COMMAND_RE.match(text.strip()))


__all__ = [
    "ACTIVITY_COMMAND_RE",
    "PARTICIPATION_COMMAND_RE",
    "is_activity_command_text",
    "is_participation_command_text",
]
