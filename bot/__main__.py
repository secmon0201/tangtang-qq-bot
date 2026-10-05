import os
import json
import importlib.util

import nonebot
from nonebot.drivers.fastapi import Driver as _FastAPIDriver  # noqa: F401
from nonebot import logger

from bot.application.plugin_registry import plugin_specs_for
from bot.config import settings
from bot.integrations.genshinuid_connector_compat import (
    install_genshinuid_connector_compatibility,
)
from bot.openapi import configure_official_environment


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
        os.environ.setdefault("gsuid_core_repeat", "true")
        if settings.gsuid_core_ws_token:
            os.environ.setdefault("gsuid_core_ws_token", settings.gsuid_core_ws_token)
    nonebot.init()
    driver = nonebot.get_driver()
    if settings.transport == "qq_openapi":
        from nonebot.adapters.qq import Adapter

        driver.register_adapter(Adapter)
        plugin_specs = plugin_specs_for(
            settings.transport,
            stats_realtime_enabled=settings.stats_realtime_enabled,
        )
        logger.info(
            "Official QQ OpenAPI mode enabled: app_id={}..., sandbox={}, port={}",
            official.app_id[:4],
            official.sandbox,
            official.port,
        )
    else:
        from nonebot.adapters.onebot.v11 import Adapter

        driver.register_adapter(Adapter)
        # Keep the registry as the only source of plugin load order.
        plugin_specs = plugin_specs_for(
            settings.transport,
            stats_realtime_enabled=settings.stats_realtime_enabled,
        )
    for plugin_spec in plugin_specs:
        if nonebot.load_plugin(plugin_spec.module) is None:
            raise RuntimeError(
                f"failed to load plugin: {plugin_spec.key} ({plugin_spec.module})"
            )
        logger.info(
            "Feature plugin loaded: {} [{}]",
            plugin_spec.label_zh,
            plugin_spec.key,
        )
    if settings.gsuid_enabled and settings.transport == "onebot":
        if importlib.util.find_spec("GenshinUID") is None:
            logger.warning(
                "GSUID_ENABLED=true but the GenshinUID Core connector is not installed; "
                "run scripts\\install_gsuid.ps1"
            )
        elif nonebot.load_plugin("GenshinUID") is None:
            logger.warning("failed to load the GenshinUID Core connector")
        else:
            install_genshinuid_connector_compatibility()
            logger.info("GenshinUID loaded with project-owned transport compatibility")
    nonebot.run()


if __name__ == "__main__":
    main()
