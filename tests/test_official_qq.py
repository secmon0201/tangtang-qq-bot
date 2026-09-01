from pathlib import Path

import nonebot
from PIL import Image


try:
    nonebot.get_driver()
except ValueError:
    nonebot.init()

from bot.plugins.official_qq import BRAND_FOOTER, BRAND_HEADER, _draw_official_probe_card


def test_official_qq_probe_uses_ak_bot_branding():
    assert BRAND_HEADER == "AK-BOT FUNCTION"
    assert BRAND_FOOTER == "AK bot"


def test_official_qq_probe_card_uses_transparent_rounded_corners(tmp_path: Path):
    path = tmp_path / "official-qq-probe.png"

    _draw_official_probe_card(path)

    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((34, 34))[3] == 255
