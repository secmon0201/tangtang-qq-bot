"""Apply human-scale delays to every visible OneBot response, including matcher replies."""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import MockApiException

from bot.services.pacing import api_call_is_paced, prepare_outbound_response, wait_for_api_turn, assert_outbound_current, OUTBOUND_RESPONSE_ACTIONS
from bot.services.runtime import database, group_domains
from bot.services.stats import StatsService


outbound_stats_service = StatsService(database(), group_provider=group_domains().all_group_ids)


@Bot.on_calling_api
async def _pace_onebot_api(_bot: Bot, action: str, _params: dict[str, object]) -> None:
    """Cover matcher.send/finish, which otherwise bypasses ``paced_call_api``."""
    if api_call_is_paced():
        return
    if not await prepare_outbound_response(action):
        # Passive messages that waited too long must never be sent as stale replies.
        raise MockApiException(None)
    await wait_for_api_turn()
    if action in OUTBOUND_RESPONSE_ACTIONS:
        assert_outbound_current()


@Bot.on_called_api
async def _record_successful_group_send(
    bot: Bot,
    exception: Exception | None,
    action: str,
    params: dict[str, object],
    result: object,
) -> None:
    """Record only confirmed bot sends; failed and cancelled API calls do not count."""
    if exception is None:
        outbound_stats_service.record_outbound_success(bot, action, params, result)
