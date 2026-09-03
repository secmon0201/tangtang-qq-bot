"""Project-owned Wuthering Waves help card built on the shared help layout."""

from pathlib import Path

from bot.config import ROOT
from bot.services.nte_help_render import NTEHelpRenderer


WUWA_HELP_PATH = ROOT / "bot" / "resources" / "wuwa_help.json"
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
    HELP_VERSION = "v1"
    CACHE_PREFIX = "wuwa_help"
    TEXTURE_DIR = WUWA_HELP_TEXTURE_DIR
    PREFIX = "#ww"
    TITLE = "XutheringWavesUID 帮助"
    TITLE_FONT_SIZE = 58
    SUBTITLE = "漂泊者，欢迎在这个时代醒来。"
    FOOTER = "Created by GsCore & Copyright by 鸣潮 · AK bot"
    COMPATIBILITY_NOTE = "兼容识别：#WW、WW、#ww、ww 均可识别"
    EXPECTED_CATEGORIES = (
        "账号绑定",
        "面板查询",
        "定制排行",
        "玩法战绩",
        "抽卡记录",
        "攻略资料",
        "个人服务",
        "其他",
    )

    def __init__(self, output_dir: Path | None = None, font_path: Path | None = None) -> None:
        super().__init__(output_dir or (ROOT / "data" / "wuwa_help_cache"), font_path)


__all__ = ["WUWA_HELP_PATH", "WuwaHelpRenderer"]
