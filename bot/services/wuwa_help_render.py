"""Project-owned Wuthering Waves help card built on the shared help layout."""

from pathlib import Path

from bot.config import ROOT
from bot.services.nte_help_render import NTEHelpRenderer
from bot.services.wuwa_help_catalog import COMPACT_CATEGORIES, FULL_CATEGORIES


WUWA_HELP_PATH = ROOT / "bot" / "resources" / "wuwa_help.json"
WUWA_FULL_HELP_PATH = ROOT / "bot" / "resources" / "wuwa_help_full.json"
WUWA_HELP_TEXTURE_DIR = (
    ROOT
    / "GsUID.Core"
    / "gsuid_core"
    / "plugins"
    / "XutheringWavesUID"
    / "XutheringWavesUID"
    / "wutheringwaves_help"
    / "texture2d"
)


class WuwaHelpRenderer(NTEHelpRenderer):
    HELP_PATH = WUWA_HELP_PATH
    HELP_VERSION = "v2"
    CACHE_PREFIX = "wuwa_help"
    TEXTURE_DIR = WUWA_HELP_TEXTURE_DIR
    PREFIX = "#ww"
    TITLE = "XutheringWavesUID 帮助"
    TITLE_FONT_SIZE = 58
    SUBTITLE = "漂泊者，欢迎在这个时代醒来。"
    FOOTER = "Created by GsCore & Copyright by 鸣潮 · AK bot"
    BADGE_TEXT = "简约帮助"
    COMPATIBILITY_NOTE = "兼容识别：#WW、WW、#ww、ww 均可识别"
    EXPECTED_CATEGORIES = COMPACT_CATEGORIES

    def __init__(self, output_dir: Path | None = None, font_path: Path | None = None) -> None:
        super().__init__(output_dir or (ROOT / "data" / "wuwa_help_cache"), font_path)


class WuwaFullHelpRenderer(WuwaHelpRenderer):
    HELP_PATH = WUWA_FULL_HELP_PATH
    HELP_VERSION = "v1"
    CACHE_PREFIX = "wuwa_help_full"
    TITLE = "XutheringWavesUID 完整帮助"
    TITLE_FONT_SIZE = 52
    SUBTITLE = "全部普通、扩展、群管理与 Bot 主人命令。"
    BADGE_TEXT = "完整帮助"
    EXPECTED_CATEGORIES = FULL_CATEGORIES


__all__ = [
    "WUWA_FULL_HELP_PATH",
    "WUWA_HELP_PATH",
    "WuwaFullHelpRenderer",
    "WuwaHelpRenderer",
]
