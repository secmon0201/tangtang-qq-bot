"""Project-owned command policy for optional Wuthering Waves extensions."""

from __future__ import annotations

import re


ROVER_REMINDER_DISABLED_MESSAGE = (
    "鸣潮邮箱体力提醒功能已关闭，当前不会保存邮箱、修改提醒设置或发送邮件。"
)

_WUWA_COMMAND_RE = re.compile(
    r"^#?\s*ww(?=$|[\s\u4e00-\u9fff]|bot|群)\s*(?P<body>.*?)\s*$",
    re.IGNORECASE,
)
_ROVER_REMINDER_RE = re.compile(
    r"^(?:"
    r"(?:开启|关闭)\s*(?:体力\s*)?推送"
    r"|(?:体力\s*)?推送邮箱.*"
    r"|(?:推送阈值|体力阈值|体力推送阈值).*"
    r")$",
    re.IGNORECASE,
)


def is_disabled_rover_reminder_command(text: str) -> bool:
    """Return whether a message targets the locally disabled mail reminder."""

    match = _WUWA_COMMAND_RE.fullmatch(str(text).strip())
    if match is None:
        return False
    return _ROVER_REMINDER_RE.fullmatch(match.group("body").strip()) is not None


__all__ = ["ROVER_REMINDER_DISABLED_MESSAGE", "is_disabled_rover_reminder_command"]
