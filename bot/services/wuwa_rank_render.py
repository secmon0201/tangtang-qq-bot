"""Compact local renderer for 100-row Wuthering Waves ranking pages."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bot.config import RESOURCE_DIR, settings
from bot.services.image_style import transparent_rounded_corners
from bot.services.wuwa_rank_data import WuwaRankResult, WuwaRankRow


class WuwaRankRenderer:
    WIDTH = 1280
    HEADER_HEIGHT = 290
    ROW_HEIGHT = 112
    ROW_GAP = 8
    FOOTER_HEIGHT = 92
    MARGIN = 30
    TEXT = "#f7fbff"
    MUTED = "#aebcc5"
    ACCENT = "#63d9cb"
    GOLD = "#f4d17a"
    SELF = "#ff9fba"

    def __init__(self, output_dir: Path | None = None, font_path: Path | None = None) -> None:
        self.output_dir = output_dir or settings.report_dir
        self.font_path = font_path or settings.report_font_path or RESOURCE_DIR / "asoul_stickers" / "font.ttf"
        self.resource_root = settings.gsuid_core_dir / "data" / "XutheringWavesUID" / "resource"
        self.texture_root = settings.gsuid_core_dir / "gsuid_core" / "plugins" / "XutheringWavesUID" / "XutheringWavesUID" / "wutheringwaves_rank" / "texture2d"
        self._fonts: dict[tuple[int, bool], ImageFont.ImageFont] = {}

    def render(self, result: WuwaRankResult, avatar_paths: Mapping[int, Path] | None = None) -> Path:
        rows = list(result.rows)
        if result.self_overflow is not None:
            rows.append(result.self_overflow)
        overflow_gap = 30 if result.self_overflow is not None else 0
        height = self.HEADER_HEIGHT + max(1, len(rows)) * self.ROW_HEIGHT + max(0, len(rows) - 1) * self.ROW_GAP + overflow_gap + self.FOOTER_HEIGHT
        image = self._background(height)
        draw = ImageDraw.Draw(image)
        self._header(image, draw, result)
        y = self.HEADER_HEIGHT
        for row in rows:
            if result.self_overflow is row:
                draw.text((self.WIDTH // 2, y + 10), "· · ·", font=self._font(24, True), fill=self.MUTED, anchor="mm")
                y += overflow_gap
            self._row(image, draw, y, row, avatar_paths or {})
            y += self.ROW_HEIGHT + self.ROW_GAP
        if not rows:
            draw.text((self.WIDTH // 2, self.HEADER_HEIGHT + self.ROW_HEIGHT // 2), "暂无本地鸣潮排行数据", font=self._font(30, True), fill=self.MUTED, anchor="mm")
        draw.text((self.WIDTH // 2, height - 42), "AK bot · 数据来自本机 XutheringWavesUID 绑定与面板缓存", font=self._font(20), fill=self.MUTED, anchor="mm")
        return self._save(image)

    def _header(self, image: Image.Image, draw: ImageDraw.ImageDraw, result: WuwaRankResult) -> None:
        title = self._open(self.texture_root / "title2.png")
        if title is not None:
            title = ImageOps.fit(title, (self.WIDTH, self.HEADER_HEIGHT), method=Image.Resampling.LANCZOS)
            title.putalpha(105)
            image.alpha_composite(title, (0, 0))
        logo = self._open(self.texture_root / "logo_small_2.png")
        if logo is not None:
            logo.thumbnail((180, 110), Image.Resampling.LANCZOS)
            image.alpha_composite(logo, (48, 42))
        draw.text((52, 170), result.title, font=self._font(52, True), fill=self.TEXT, anchor="lm")
        badge_left = min(1030, 72 + self._text_width(result.title, self._font(52, True)))
        draw.rounded_rectangle((badge_left, 148, badge_left + 170, 194), radius=20, fill="#236e69", outline=self.ACCENT, width=2)
        draw.text((badge_left + 85, 171), "糖糖接管", font=self._font(21, True), fill=self.TEXT, anchor="mm")
        draw.text((54, 240), f"{result.scope_label} · {result.total} 条 · 第 {result.request.page}/{result.total_pages} 页 · 每页 100 条", font=self._font(24, True), fill=self.MUTED, anchor="lm")

    def _row(self, image: Image.Image, draw: ImageDraw.ImageDraw, top: int, row: WuwaRankRow, avatar_paths: Mapping[int, Path]) -> None:
        fill = (18, 31, 38, 232) if row.rank % 2 else (23, 40, 47, 232)
        outline = self.SELF if row.is_self else (self.GOLD if row.rank <= 3 else "#477069")
        draw.rounded_rectangle((self.MARGIN, top, self.WIDTH - self.MARGIN, top + self.ROW_HEIGHT), radius=8, fill=fill, outline=outline, width=2)
        draw.text((70, top + 56), str(row.rank), font=self._font(34, True), fill=self.GOLD if row.rank <= 3 else self.TEXT, anchor="mm")
        qq_path = avatar_paths.get(_int_or_zero(row.user_id))
        self._avatar(image, draw, qq_path, 104, top + 20, 72, row.nickname)
        draw.text((194, top + 31), self._fit(row.nickname, self._font(25, True), 205), font=self._font(25, True), fill=self.SELF if row.is_self else self.TEXT)
        draw.text((194, top + 67), self._fit(f"QQ {row.user_id}", self._font(17), 205), font=self._font(17), fill=self.MUTED)
        draw.text((194, top + 91), self._fit(row.group_name, self._font(17), 205), font=self._font(17), fill=self.MUTED)
        char_path = self.resource_root / "waves_avatar" / f"role_head_{row.char_id}.png"
        self._avatar(image, draw, char_path, 420, top + 20, 72, row.char_name)
        draw.text((508, top + 30), self._fit(row.char_name, self._font(24, True), 175), font=self._font(24, True), fill=self.TEXT)
        draw.text((508, top + 66), f"Lv{row.level} · {row.chain}链", font=self._font(18), fill=self.MUTED)
        draw.text((508, top + 91), f"UID {row.uid}", font=self._font(16), fill=self.MUTED)
        draw.text((700, top + 32), self._fit(row.weapon_name, self._font(22, True), 215), font=self._font(22, True), fill=self.TEXT)
        draw.text((700, top + 70), f"武器 Lv{row.weapon_level} · 谐振 {row.weapon_resonance}", font=self._font(17), fill=self.MUTED)
        draw.text((930, top + 32), self._fit(row.sonata_name, self._font(20, True), 180), font=self._font(20, True), fill=self.TEXT)
        draw.text((930, top + 70), "声骸套装", font=self._font(17), fill=self.MUTED)
        score = f"{row.score:.1f}".rstrip("0").rstrip(".")
        draw.text((1212, top + 45), score, font=self._font(38, True), fill=self.ACCENT, anchor="rm")
        draw.text((1212, top + 79), "评分", font=self._font(17), fill=self.MUTED, anchor="rm")

    def _background(self, height: int) -> Image.Image:
        texture = self._open(settings.gsuid_core_dir / "gsuid_core" / "plugins" / "XutheringWavesUID" / "XutheringWavesUID" / "wutheringwaves_help" / "texture2d" / "bg.jpg")
        if texture is None:
            return Image.new("RGBA", (self.WIDTH, height), "#0e1b20")
        canvas = Image.new("RGBA", (self.WIDTH, height), "#0e1b20")
        tile = ImageOps.fit(texture, (self.WIDTH, max(1, texture.height)), method=Image.Resampling.LANCZOS)
        for y in range(0, height, tile.height):
            canvas.alpha_composite(tile, (0, y))
        canvas.alpha_composite(Image.new("RGBA", canvas.size, (2, 12, 15, 94)))
        return canvas

    def _avatar(self, image: Image.Image, draw: ImageDraw.ImageDraw, path: Path | None, left: int, top: int, size: int, label: str) -> None:
        avatar = self._open(path)
        if avatar is None:
            avatar = Image.new("RGBA", (size, size), "#31534f")
            ImageDraw.Draw(avatar).text((size // 2, size // 2), (label or "?")[-2:], font=self._font(19, True), fill=self.TEXT, anchor="mm")
        avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        image.paste(avatar, (left, top), mask)
        draw.ellipse((left, top, left + size - 1, top + size - 1), outline="#8ccbc2", width=2)

    @staticmethod
    def _open(path: Path | None) -> Image.Image | None:
        if path is None:
            return None
        try:
            with Image.open(path) as source:
                return source.convert("RGBA")
        except (OSError, ValueError):
            return None

    def _font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        key = (size, bold)
        if key in self._fonts:
            return self._fonts[key]
        candidates = [Path(r"C:\Windows\Fonts\msyhbd.ttc") if bold else Path(r"C:\Windows\Fonts\msyh.ttc"), self.font_path]
        for candidate in candidates:
            if candidate and candidate.exists():
                try:
                    self._fonts[key] = ImageFont.truetype(str(candidate), size=size)
                    return self._fonts[key]
                except OSError:
                    continue
        self._fonts[key] = ImageFont.load_default(size=size)
        return self._fonts[key]

    def _fit(self, value: str, font: ImageFont.ImageFont, width: int) -> str:
        text = value or "未记录"
        if self._text_width(text, font) <= width:
            return text
        while text and self._text_width(text + "...", font) > width:
            text = text[:-1]
        return (text or "...") + "..."

    @staticmethod
    def _text_width(value: str, font: ImageFont.ImageFont) -> int:
        box = font.getbbox(value)
        return box[2] - box[0]

    def _save(self, image: Image.Image) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now() - timedelta(hours=settings.report_retention_hours)
        for old in self.output_dir.glob("wuwa_rank_*.png"):
            try:
                if datetime.fromtimestamp(old.stat().st_mtime) < cutoff:
                    old.unlink(missing_ok=True)
            except OSError:
                continue
        path = self.output_dir / f"wuwa_rank_{uuid4().hex}.png"
        transparent_rounded_corners(image).save(path, format="PNG", optimize=True)
        return path


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = ["WuwaRankRenderer"]
