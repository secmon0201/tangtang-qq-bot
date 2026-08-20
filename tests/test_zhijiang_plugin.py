import importlib

import nonebot


def test_zhijiang_plugin_registers_without_fetching_network_data():
    nonebot.init()
    plugin = importlib.import_module("bot.plugins.zhijiang")
    assert plugin.guard.enabled
    assert plugin.guard.refresh_minutes >= 1
    assert plugin.guard.pause_duration.total_seconds() == 3600
