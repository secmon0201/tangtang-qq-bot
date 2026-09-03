"""Local NTEUID help image renderer in the upstream visual language."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bot.config import RESOURCE_DIR, ROOT, settings
from bot.services.image_style import transparent_rounded_corners


HELP_VERSION = "v9"
HELP_PATH = ROOT / "bot" / "resources" / "nte_help.json"
UPSTREAM_HELP_DIR = ROOT / "GsUID.Core" / "gsuid_core" / "plugins" / "NTEUID" / "NTEUID" / "nte_help"
TEXTURE_DIR = UPSTREAM_HELP_DIR / "texture2d"
TANGTANG_AVATAR = ROOT / "bot" / "resources" / "tangtang_avatar.jpg"
ASOUL_STICKER_DIR = RESOURCE_DIR / "asoul_stickers"


class NTEHelpRenderer:
    """Keep the local command list while matching the upstream NTEUID help card."""

    WIDTH = 2048
    BANNER_HEIGHT = 760
    CARD_WIDTH = 447
    CARD_HEIGHT = 150
    COLUMN_GAP = 45
    SECTION_HEIGHT = 96
    SECTION_GAP = 42
    MARGIN = 76
    PINK = "#ff2f70"
    TANGTANG_PINK = "#f39abb"
    CARD_FILL = "#252525"
    TEXT = "#ffffff"
    MUTED = "#d2d2d2"
    FOOTER_HEIGHT = 128
    COMPATIBILITY_NOTE = "兼容识别：#NTE、NTE、#nte、nte 均可识别"
    HELP_PATH = HELP_PATH
    HELP_VERSION = HELP_VERSION
    CACHE_PREFIX = "nte_help"
    TEXTURE_DIR = TEXTURE_DIR
    PREFIX = "#nte"
    TITLE = "NTEUID 帮助"
    SUBTITLE = "一切正常，就是异常。"
    FOOTER = "Created by GsCore & Copyright by 异环"
    BADGE_TEXT = "糖糖接管"
    TITLE_FONT_SIZE = 70
    EXPECTED_CATEGORIES = ("登录绑定", "信息查询", "定制排行", "配队攻略", "签到服务", "抽卡记录", "其他")

    def __init__(self, output_dir: Path | None = None, font_path: Path | None = None) -> None:
        self.output_dir = output_dir or (ROOT / "data" / "nte_help_cache")
        self.font_path = font_path or settings.report_font_path or ASOUL_STICKER_DIR / "font.ttf"
        self._font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}
        self._stickers: dict[str, tuple[Path, ...]] = {}

    @classmethod
    def load_data(cls, path: Path | None = None) -> dict[str, Any]:
        path = path or cls.HELP_PATH
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or tuple(data) != cls.EXPECTED_CATEGORIES:
            raise ValueError(f"{path.name} 的帮助分类不符合渲染器约定")
        for category, value in data.items():
            if not isinstance(value, dict) or not isinstance(value.get("data"), list) or not value["data"]:
                raise ValueError(f"帮助分类无有效命令：{category}")
            for entry in value["data"]:
                if not all(isinstance(entry.get(key), str) for key in ("name", "desc", "eg")):
                    raise ValueError(f"帮助条目字段不完整：{category}")
                if "sticker_group" in entry and not isinstance(entry["sticker_group"], str):
                    raise ValueError(f"帮助条目表情包分组无效：{category}")
        return data

    def render(self, *, force: bool = False) -> Path:
        cached = self.output_dir / f"{self.CACHE_PREFIX}_{self.HELP_VERSION}.png"
        if cached.exists() and not force:
            return cached
        data = self.load_data()
        rows_per_section = [(len(value["data"]) + 3) // 4 for value in data.values()]
        height = self.BANNER_HEIGHT + sum(
            self.SECTION_HEIGHT + count * self.CARD_HEIGHT + max(0, count - 1) * 26 + self.SECTION_GAP
            for count in rows_per_section
        ) + self.FOOTER_HEIGHT
        image = self._background(height)
        draw = ImageDraw.Draw(image)
        self._draw_banner(image, draw)
        y = self.BANNER_HEIGHT
        icon_index = 155
        for category, section in data.items():
            count = (len(section["data"]) + 3) // 4
            self._draw_section(image, draw, y, category, str(section["desc"]), section["data"], icon_index)
            icon_index += len(section["data"])
            y += self.SECTION_HEIGHT + count * self.CARD_HEIGHT + max(0, count - 1) * 26 + self.SECTION_GAP
        draw.text((self.WIDTH // 2, height - 78), self.COMPATIBILITY_NOTE, font=self._font(26, True), fill=self.TEXT, anchor="mm")
        draw.text((self.WIDTH // 2, height - 32), self.FOOTER, font=self._font(22), fill=self.MUTED, anchor="mm")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        transparent_rounded_corners(image).save(cached, format="PNG", optimize=True)
        return cached

    def _draw_banner(self, image: Image.Image, draw: ImageDraw.ImageDraw) -> None:
        banner = self._open_image(self.TEXTURE_DIR / "banner_bg.jpg")
        if banner is not None:
            image.paste(ImageOps.fit(banner, (self.WIDTH, self.BANNER_HEIGHT), method=Image.Resampling.LANCZOS), (0, 0))
        else:
            draw.rectangle((0, 0, self.WIDTH, self.BANNER_HEIGHT), fill="#101725")
        image.alpha_composite(Image.new("RGBA", (self.WIDTH, self.BANNER_HEIGHT), (5, 9, 16, 96)))
        icon = self._open_image(TANGTANG_AVATAR)
        if icon is not None:
            icon = ImageOps.fit(icon, (142, 142), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", icon.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, icon.width - 1, icon.height - 1), fill=255)
            image.paste(icon, (self.MARGIN + 40, 450), mask)
            draw.ellipse((self.MARGIN + 40, 450, self.MARGIN + 181, 591), outline="#ffffff", width=3)
        title_left = self.MARGIN + 210
        title_font = self._font(self.TITLE_FONT_SIZE, True)
        draw.text((title_left, 465), self.TITLE, font=title_font, fill=self.TEXT)
        badge_left = min(self.WIDTH - 250, title_left + self._text_width(self.TITLE, title_font) + 42)
        draw.rounded_rectangle((badge_left, 470, badge_left + 195, 542), radius=24, fill="#ff3d4c")
        draw.text((badge_left + 98, 506), self.BADGE_TEXT, font=self._font(30, True), fill=self.TEXT, anchor="mm")
        draw.text((self.MARGIN + 210, 568), self.SUBTITLE, font=self._font(42, True), fill=self.MUTED)

    def _draw_section(self, image: Image.Image, draw: ImageDraw.ImageDraw, top: int, category: str, desc: str, entries: list[dict[str, str]], icon_index: int) -> None:
        strip = self._open_image(self.TEXTURE_DIR / "cag_bg.png")
        if strip is not None:
            image.paste(ImageOps.fit(strip, (self.WIDTH - self.MARGIN * 2, self.SECTION_HEIGHT), method=Image.Resampling.LANCZOS), (self.MARGIN, top))
        else:
            draw.rounded_rectangle((self.MARGIN, top, self.WIDTH - self.MARGIN, top + self.SECTION_HEIGHT), radius=42, fill="#272727")
        title_fill = self.TANGTANG_PINK if category == "定制排行" else self.PINK
        draw.rounded_rectangle((self.MARGIN, top, self.MARGIN + 500, top + self.SECTION_HEIGHT), radius=42, fill=title_fill)
        draw.polygon(((self.MARGIN + 470, top), (self.MARGIN + 620, top), (self.MARGIN + 530, top + self.SECTION_HEIGHT), (self.MARGIN + 380, top + self.SECTION_HEIGHT)), fill="#272727")
        draw.text((self.MARGIN + 36, top + 16), "›", font=self._font(70, True), fill=self.TEXT)
        draw.text((self.MARGIN + 106, top + 20), category, font=self._font(48, True), fill=self.TEXT)
        draw.text((self.MARGIN + 430, top + 33), self._ellipsize(desc, self._font(30, True), 760), font=self._font(30, True), fill=self.MUTED)
        for index, entry in enumerate(entries):
            row, column = divmod(index, 4)
            left = self.MARGIN + column * (self.CARD_WIDTH + self.COLUMN_GAP)
            card_top = top + self.SECTION_HEIGHT + 28 + row * (self.CARD_HEIGHT + 26)
            self._draw_card(image, draw, left, card_top, entry, icon_index + index)

    def _draw_card(self, image: Image.Image, draw: ImageDraw.ImageDraw, left: int, top: int, entry: dict[str, str], icon_index: int) -> None:
        item = self._open_image(self.TEXTURE_DIR / "item.png")
        if item is not None:
            card = ImageOps.fit(item, (self.CARD_WIDTH, self.CARD_HEIGHT), method=Image.Resampling.LANCZOS)
            image.paste(card, (left, top), card)
        else:
            draw.rounded_rectangle((left, top, left + self.CARD_WIDTH, top + self.CARD_HEIGHT), radius=34, fill=self.CARD_FILL, outline=self.PINK, width=3)
        sticker = self._sticker(str(entry.get("sticker_group") or ""), icon_index)
        if sticker is not None:
            icon = ImageOps.contain(sticker, (126, 126), method=Image.Resampling.LANCZOS)
            image.paste(icon, (left + 20 + (126 - icon.width) // 2, top + 12 + (126 - icon.height) // 2), icon)
        else:
            draw.ellipse((left + 26, top + 28, left + 112, top + 114), fill="#4b4b4b")
            draw.text((left + 69, top + 70), entry["name"][:1], font=self._font(32, True), fill=self.TEXT, anchor="mm")
        text_left = left + 172
        text_width = self.CARD_WIDTH - 192
        draw.text((text_left, top + 30), self._ellipsize(entry["name"], self._font(30, True), text_width), font=self._font(30, True), fill=self.TEXT)
        draw.text((text_left, top + 84), self._ellipsize(self.PREFIX + entry["eg"], self._font(22, True), text_width), font=self._font(22, True), fill=self.MUTED)

    def _sticker(self, group: str, index: int) -> Image.Image | None:
        if group:
            paths = self._stickers.get(group)
            if paths is None:
                paths = tuple(sorted((ASOUL_STICKER_DIR / group).glob("*.png")))
                self._stickers[group] = paths
        else:
            paths = self._stickers.get("")
            if paths is None:
                paths = tuple(sorted(ASOUL_STICKER_DIR.glob("*/*.png")))
                self._stickers[""] = paths
        if not paths:
            return None
        return self._open_image(paths[(index - 155) % len(paths)])

    def _background(self, height: int) -> Image.Image:
        texture = self._open_image(self.TEXTURE_DIR / "bg.jpg")
        if texture is None:
            return Image.new("RGBA", (self.WIDTH, height), "#111111")
        canvas = Image.new("RGBA", (self.WIDTH, height))
        for y in range(0, height, texture.height):
            for x in range(0, self.WIDTH, texture.width):
                canvas.paste(texture, (x, y))
        return canvas

    @staticmethod
    def _open_image(path: Path) -> Image.Image | None:
        try:
            with Image.open(path) as source:
                return source.convert("RGBA")
        except (OSError, ValueError):
            return None

    def _font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        candidates = [self.font_path]
        if bold:
            candidates += [Path(r"C:\Windows\Fonts\msyhbd.ttc"), Path(r"C:\Windows\Fonts\Dengb.ttf")]
        candidates += [Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\Deng.ttf"), Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")]
        for candidate in candidates:
            if candidate and candidate.exists():
                try:
                    font = ImageFont.truetype(str(candidate), size=size)
                    self._font_cache[key] = font
                    return font
                except OSError:
                    continue
        font = ImageFont.load_default(size=size)
        self._font_cache[key] = font
        return font

    def _ellipsize(self, value: str, font: ImageFont.ImageFont, width: int) -> str:
        if self._text_width(value, font) <= width:
            return value
        while value and self._text_width(value + "...", font) > width:
            value = value[:-1]
        return (value or "...") + "..."

    @staticmethod
    def _text_width(value: str, font: ImageFont.ImageFont) -> int:
        box = font.getbbox(value)
        return box[2] - box[0]


__all__ = ["HELP_PATH", "HELP_VERSION", "NTEHelpRenderer"]
