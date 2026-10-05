"""Platform boundary for all QQ transport operations.

Business modules depend on this service instead of a specific QQ client. The
current implementation speaks OneBot v11, so compatible local gateways can sit
behind it. Adapter-specific response differences stay inside this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tangtang_harness.business.protocol import paced_call_api


class QQPlatformError(RuntimeError):
    """Raised when the configured QQ transport cannot complete an operation."""


def _data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


class QQPlatform:
    """QQ operations required by this application, backed by OneBot v11 today."""

    def __init__(self, bot: Any) -> None:
        self._bot = bot

    @property
    def self_id(self) -> str:
        return str(self._bot.self_id)

    async def _request(self, action: str, **params: Any) -> Any:
        try:
            return _data(await paced_call_api(self._bot, action, **params))
        except Exception as exc:
            raise QQPlatformError(f"{action} failed: {exc}") from exc

    async def call(self, action: str, **params: Any) -> Any:
        """Temporary compatibility entry point for standard OneBot v11 actions.

        New platform-specific behavior belongs in a named method above.  This
        keeps existing, proven commands functional while their small action
        wrappers are migrated one at a time.
        """

        return await self._request(action, **params)

    async def send_group_message(self, group_id: int, message: Any) -> Any:
        return await self._request("send_group_msg", group_id=int(group_id), message=message)

    async def send_private_message(self, user_id: int, message: Any) -> Any:
        return await self._request("send_private_msg", user_id=int(user_id), message=message)

    async def send_group_forward(self, group_id: int, messages: Any) -> Any:
        return await self._request("send_group_forward_msg", group_id=int(group_id), messages=messages)

    async def send_private_forward(self, user_id: int, messages: Any) -> Any:
        return await self._request("send_private_forward_msg", user_id=int(user_id), messages=messages)

    async def login_info(self) -> dict[str, Any]:
        data = await self._request("get_login_info")
        if not isinstance(data, dict) or not data.get("user_id"):
            raise QQPlatformError("invalid login info response")
        return data

    async def add_reaction(self, message_id: int, emoji_id: str) -> Any:
        return await self._request(
            "set_msg_emoji_like", message_id=int(message_id), emoji_id=str(emoji_id), set=True
        )

    async def withdraw_message(self, message_id: int) -> Any:
        return await self._request("delete_msg", message_id=int(message_id))

    async def set_group_ban(self, group_id: int, user_id: int, duration: int) -> Any:
        return await self._request(
            "set_group_ban", group_id=int(group_id), user_id=int(user_id), duration=int(duration)
        )

    async def group_info(self, group_id: int) -> dict[str, Any]:
        data = await self._request("get_group_info", group_id=int(group_id), no_cache=False)
        if not isinstance(data, dict):
            raise QQPlatformError(f"invalid group info response for {group_id}")
        return data

    async def group_list(self) -> list[dict[str, Any]]:
        data = await self._request("get_group_list", no_cache=False)
        if not isinstance(data, list):
            raise QQPlatformError("invalid group list response")
        return [item for item in data if isinstance(item, dict) and item.get("group_id")]

    async def member_list(self, group_id: int) -> list[dict[str, Any]]:
        data = await self._request("get_group_member_list", group_id=int(group_id), no_cache=False)
        if not isinstance(data, list):
            raise QQPlatformError(f"invalid member list response for {group_id}")
        return [item for item in data if isinstance(item, dict) and "user_id" in item]

    async def member_info(self, group_id: int, user_id: int) -> dict[str, Any]:
        data = await self._request(
            "get_group_member_info", group_id=int(group_id), user_id=int(user_id), no_cache=False
        )
        if not isinstance(data, dict):
            raise QQPlatformError(f"invalid member info response for {group_id}/{user_id}")
        return data

    async def message_history(self, group_id: int, count: int = 200) -> list[dict[str, Any]]:
        data = await self._request(
            "get_group_msg_history",
            group_id=int(group_id),
            count=int(count),
            reverse_order=True,
        )
        if isinstance(data, dict):
            data = data.get("messages", data.get("message", []))
        if not isinstance(data, list):
            raise QQPlatformError(f"invalid message history response for {group_id}")
        return [item for item in data if isinstance(item, dict)]

    async def web_cookies(self, domain: str = "qun.qq.com") -> dict[str, str]:
        data = await self._request("get_cookies", domain=domain)
        if not isinstance(data, dict):
            raise QQPlatformError("invalid QQ web cookies response")
        cookies = data.get("cookies")
        if not isinstance(cookies, str) or not cookies.strip():
            raise QQPlatformError("QQ web cookies are empty")
        bkn = data.get("bkn")
        if bkn in {None, ""}:
            csrf = await self._request("get_csrf_token")
            if isinstance(csrf, dict):
                bkn = csrf.get("token", csrf.get("csrf_token"))
        return {"cookies": cookies, "bkn": str(bkn or "")}

    async def sync_members(self, group_ids: Iterable[int], database: Any) -> list[int]:
        failed: list[int] = []
        for group_id in group_ids:
            try:
                info = await self.group_info(group_id)
                members = await self.member_list(group_id)
                database.set_group_info(group_id, str(info.get("group_name") or group_id))
                database.replace_members(group_id, members)
            except QQPlatformError:
                database.set_stats_capability(group_id, False, "unknown")
                failed.append(int(group_id))
        return failed

    async def refresh_stats_capabilities(self, group_ids: Iterable[int], database: Any) -> list[int]:
        failed: list[int] = []
        self_id = int(self.self_id)
        for group_id in group_ids:
            try:
                members = await self.member_list(group_id)
                member = next(
                    (item for item in members if int(item.get("user_id", 0)) == self_id), None
                )
                if member is None:
                    database.set_stats_capability(group_id, False, "not_member")
                    continue
                role = str(member.get("role") or "member")
                database.set_stats_capability(group_id, role in {"owner", "admin"}, role)
            except QQPlatformError:
                failed.append(int(group_id))
        return failed


def qq_platform(bot: Any) -> QQPlatform:
    """Build a transport-neutral platform facade for a connected QQ bot."""

    return QQPlatform(bot)


async def call_qq_action(bot: Any, action: str, **params: Any) -> Any:
    """Compatibility bridge used while commands move to named platform methods."""

    return await qq_platform(bot).call(action, **params)
