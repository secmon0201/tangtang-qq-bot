"""A protocol port for migrated QQ business methods; scheduling lives in Harness."""
from typing import Any


async def paced_call_api(bot: Any, action: str, **params: Any) -> Any:
    return await bot.call_api(action, **params)
