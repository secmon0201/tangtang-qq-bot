from __future__ import annotations

import nonebot

nonebot.init()

from bot.plugins.tangtang_proactive import _status_text  # noqa: E402
from bot.services.tangtang_chat import TangtangConfig  # noqa: E402


def test_proactive_status_text_uses_env_config_values():
    config = TangtangConfig.disabled("test")
    text = _status_text(config)
    assert "糖糖主动回复当前配置" in text
    assert "糖糖总开关：关闭" in text
    assert "主动回复：关闭" in text
    assert "命中率：2%" in text
    assert "冷却：30 分钟" in text
    assert "消息间隔：30 条" in text
