from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Protocol


class LiveEntry(Protocol):
    category: str
    title: str


SINGLE_LIVE_GAME_REMINDERS = (
    "{names}正在直播，小游戏先放放，别分神影响大家聊直播。",
    "{names}开播啦，先看直播，小游戏晚点再玩。",
    "{names}正在播呢，小游戏先歇会儿，别错过直播。",
    "{names}的直播开始了，先别惦记小游戏，来一起看直播。",
    "{names}正在直播，先把小游戏放一边，大家正聊得热闹呢。",
    "{names}开播中，小游戏先等等，咱先看直播。",
    "直播优先，{names}正在播呢，小游戏一会儿再开。",
    "{names}正在直播，别光顾着玩小游戏啦，来看看直播。",
    "{names}开播了，小游戏先暂停，别把直播讨论带跑偏。",
    "{names}正在播，先专心看直播，小游戏晚点继续。",
)

MULTI_LIVE_GAME_REMINDERS = (
    "{names}都在直播，小游戏先放放，别分神影响大家聊直播。",
    "{names}一起开播啦，先看直播，小游戏晚点再玩。",
    "{names}都在播呢，小游戏先歇会儿，别错过直播。",
    "{names}的直播都开始了，先别惦记小游戏，来一起看直播。",
    "{names}正在直播，先把小游戏放一边，大家正聊得热闹呢。",
    "{names}都开播中，小游戏先等等，咱先看直播。",
    "直播优先，{names}都在播呢，小游戏一会儿再开。",
    "{names}都在直播，别光顾着玩小游戏啦，来看看直播。",
    "{names}都开播了，小游戏先暂停，别把直播讨论带跑偏。",
    "{names}正在播，先专心看直播，小游戏晚点继续。",
)


def live_member_names(entries: Sequence[LiveEntry]) -> tuple[str, ...]:
    """Use each schedule category as the member name, preserving start order."""
    names: list[str] = []
    for entry in entries:
        category = entry.category.strip()
        title = entry.title.strip()
        name = category if category and category != "其他" else title
        if name and name not in names:
            names.append(name)
    return tuple(names)


def format_live_member_names(names: Sequence[str]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    return "、".join(names[:-1]) + "和" + names[-1]


def render_live_game_reminder(
    entries: Sequence[LiveEntry],
    *,
    choose: Callable[[Sequence[str]], str] = random.choice,
) -> str | None:
    names = live_member_names(entries)
    if not names:
        return None
    templates = SINGLE_LIVE_GAME_REMINDERS if len(names) == 1 else MULTI_LIVE_GAME_REMINDERS
    return choose(templates).format(names=format_live_member_names(names))
