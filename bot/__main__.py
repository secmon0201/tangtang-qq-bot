import os
import json
import importlib.util

import nonebot
from nonebot.drivers.fastapi import Driver as _FastAPIDriver  # noqa: F401
from nonebot import logger

from bot.config import settings
from bot.openapi import configure_official_environment
from bot.services.nte_prefix_display import patch_upstream_nte_prefix


def main() -> None:
    os.environ.setdefault("HOST", settings.host)
    if settings.transport == "qq_openapi":
        official = configure_official_environment(settings)
        os.environ["COMMAND_START"] = json.dumps(["#"])
    else:
        os.environ.setdefault("PORT", str(settings.port))
        os.environ["COMMAND_START"] = json.dumps([settings.command_prefix])
    if settings.gsuid_enabled:
        # GenshinUID reads these values from NoneBot's driver config. Keep the
        # project-specific .env names isolated from the upstream connector.
        os.environ.setdefault("gsuid_core_host", settings.gsuid_core_host)
        os.environ.setdefault("gsuid_core_port", str(settings.gsuid_core_port))
        os.environ.setdefault("gsuid_core_botid", settings.gsuid_core_botid)
        if settings.gsuid_core_ws_token:
            os.environ.setdefault("gsuid_core_ws_token", settings.gsuid_core_ws_token)
    nonebot.init()
    driver = nonebot.get_driver()
    if settings.transport == "qq_openapi":
        from nonebot.adapters.qq import Adapter

        driver.register_adapter(Adapter)
        plugin_names = ["bot.plugins.official_qq"]
        logger.info(
            "Official QQ OpenAPI mode enabled: app_id=%s..., sandbox=%s, port=%s",
            official.app_id[:4],
            official.sandbox,
            official.port,
        )
    else:
        from nonebot.adapters.onebot.v11 import Adapter

        driver.register_adapter(Adapter)
        # Load by module name so a PyInstaller one-file build does not depend on
        # raw plugin source files being present in the temporary extraction path.
        plugin_names = [
            "bot.plugins.outbound_pacing",
            "bot.plugins.scope",
            "bot.plugins.game_api",
            "bot.plugins.nte_game_ui",
            "bot.plugins.napcat_maintenance",
            "bot.plugins.qq_platform_health",
            "bot.plugins.random_reactions",
            "bot.plugins.tangtang_chat",
            "bot.plugins.commands",
            "bot.plugins.knowledge_review",
            "bot.plugins.mini_games",
            "bot.plugins.today_wife",
            "bot.plugins.codex_completion",
            "bot.plugins.zhijiang",
            "bot.plugins.asoul",
            "bot.plugins.activities",
            "bot.plugins.surveys",
            "bot.plugins.global_announcement",
            "bot.plugins.hourly_announcements",
        ]
        if settings.stats_realtime_enabled:
            plugin_names.insert(2, "bot.plugins.stats")
            plugin_names.insert(plugin_names.index("bot.plugins.commands") + 1, "bot.plugins.a_coast_archive")
    for plugin_name in plugin_names:
        if nonebot.load_plugin(plugin_name) is None:
            raise RuntimeError(f"failed to load plugin: {plugin_name}")
    if settings.gsuid_enabled and settings.transport == "onebot":
        if not patch_upstream_nte_prefix():
            logger.warning("NTEUID display prefix patch skipped: upstream package is unavailable")
        if importlib.util.find_spec("GenshinUID") is None:
            logger.warning(
                "GSUID_ENABLED=true but the GenshinUID Core connector is not installed; "
                "run scripts\\install_gsuid.ps1"
            )
        elif nonebot.load_plugin("GenshinUID") is None:
            logger.warning("failed to load the GenshinUID Core connector")
    nonebot.run()


if __name__ == "__main__":
    main()
