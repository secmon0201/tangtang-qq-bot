"""Generate three portrait layout directions for QQ robot report posters.

These are review mockups only.  They deliberately use the same status content so
that visual hierarchy, spacing, and palette can be compared directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH = 768
OUTPUT_DIR = Path("reports/design_proposals")
FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")


@dataclass(frozen=True)
class Palette:
    name: str
    background: str
    surface: str
    ink: str
    muted: str
    border: str
    accent: str
    colors: tuple[str, str, str, str]
    title_fill: str
    card_tint: str


PALETTES = (
    Palette(
        "01_pastel_blocks",
        "#fff7ef", "#fffdfa", "#2c2930", "#766d72", "#ebd9cc", "#ee7858",
        ("#ffb79e", "#ffd579", "#86d1c2", "#a8bbed"), "#f7e2cf", "#fff5ed",
    ),
    Palette(
        "02_candy_ribbon",
        "#fff8fc", "#fffefe", "#312941", "#7e718d", "#e9d7e6", "#d65791",
        ("#f28aac", "#c6a3e6", "#70c9d6", "#ffe07b"), "#f7dded", "#fff2f8",
    ),
    Palette(
        "03_fresh_mint",
        "#f4fbf8", "#ffffff", "#273530", "#687a72", "#d6e9e0", "#278a78",
        ("#83d4c6", "#b4d9ff", "#ffd596", "#f6a5b9"), "#dff3eb", "#effaf6",
    ),
)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold and FONT_BOLD.exists() else FONT_REGULAR
    return ImageFont.truetype(path, size=size)


def text_width(draw: ImageDraw.ImageDraw, value: str, used_font: ImageFont.ImageFont) -> int:
    return draw.textbbox((0, 0), value, font=used_font)[2]


def wrap(draw: ImageDraw.ImageDraw, value: str, used_font: ImageFont.ImageFont, max_width: int) -> list[str]:
    result: list[str] = []
    for paragraph in value.splitlines() or [""]:
        line = ""
        for character in paragraph:
            candidate = line + character
            if line and text_width(draw, candidate, used_font) > max_width:
                result.append(line)
                line = character
            else:
                line = candidate
        result.append(line)
    return result


def draw_lines(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    lines: list[str],
    used_font: ImageFont.ImageFont,
    color: str,
    gap: int = 10,
) -> int:
    line_height = draw.textbbox((0, 0), "枝江", font=used_font)[3] + gap
    for line in lines:
        draw.text((x, y), line, font=used_font, fill=color)
        y += line_height
    return y


def rounded_matrix_title(draw: ImageDraw.ImageDraw, palette: Palette) -> int:
    """A compact title plate made from rounded, colorful matrix cells."""
    draw.rounded_rectangle((52, 80, WIDTH - 52, 230), radius=32, fill=palette.title_fill)
    draw.rounded_rectangle((62, 90, WIDTH - 62, 220), radius=26, outline=palette.border, width=2)
    brand_font = font(16, True)
    brand_width = text_width(draw, "AK-BOT FUNCTION", brand_font) + 38
    draw.rounded_rectangle((82, 107, 82 + brand_width, 139), radius=16, fill=palette.surface)
    draw.text((101, 113), "AK-BOT FUNCTION", font=brand_font, fill=palette.accent)
    draw.text((82, 146), "枝江直播状态", font=font(48, True), fill=palette.ink)
    draw.text((82, 207), "直播防护运行快照  |  2026-07-28 01:56", font=font(17), fill=palette.muted)
    draw_bow(draw, WIDTH - 128, 135, 30, "#b397d0", "#79588b")
    draw_heart(draw, WIDTH - 76, 108, 20, "#d987a4")
    draw_heart(draw, WIDTH - 50, 145, 12, palette.colors[1])
    return 260


def draw_corner_accents(draw: ImageDraw.ImageDraw, palette: Palette, height: int) -> None:
    """Keep color at the four corners, leaving the page edges quiet."""
    margin, band, span = 0, 11, 210
    fills = (
        ((margin, margin), (margin + span, margin), (margin + span, margin + band), (margin + band, margin + band), (margin + band, margin + span), (margin, margin + span)),
        ((WIDTH - margin, margin), (WIDTH - margin - span, margin), (WIDTH - margin - span, margin + band), (WIDTH - margin - band, margin + band), (WIDTH - margin - band, margin + span), (WIDTH - margin, margin + span)),
        ((margin, height - margin), (margin + span, height - margin), (margin + span, height - margin - band), (margin + band, height - margin - band), (margin + band, height - margin - span), (margin, height - margin - span)),
        ((WIDTH - margin, height - margin), (WIDTH - margin - span, height - margin), (WIDTH - margin - span, height - margin - band), (WIDTH - margin - band, height - margin - band), (WIDTH - margin - band, height - margin - span), (WIDTH - margin, height - margin - span)),
    )
    for points, color in zip(fills, palette.colors):
        draw.polygon(points, fill=color)


def draw_heart(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str) -> None:
    radius = max(3, size // 4)
    draw.ellipse((center_x - radius * 2, center_y - radius, center_x, center_y + radius), fill=fill)
    draw.ellipse((center_x, center_y - radius, center_x + radius * 2, center_y + radius), fill=fill)
    draw.polygon(((center_x - radius * 2, center_y), (center_x + radius * 2, center_y), (center_x, center_y + size)), fill=fill)


def draw_bow(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str, center_fill: str) -> None:
    half = size // 2
    draw.ellipse((center_x - size, center_y - half, center_x - 4, center_y + half), fill=fill)
    draw.ellipse((center_x + 4, center_y - half, center_x + size, center_y + half), fill=fill)
    draw.polygon(((center_x - half, center_y + 8), (center_x - 4, center_y + half + 20), (center_x, center_y + 4)), fill=fill)
    draw.polygon(((center_x + half, center_y + 8), (center_x + 4, center_y + half + 20), (center_x, center_y + 4)), fill=fill)
    draw.rounded_rectangle((center_x - 8, center_y - 11, center_x + 8, center_y + 11), radius=5, fill=center_fill)


def tag(draw: ImageDraw.ImageDraw, right: int, y: int, label: str, fill: str, ink: str) -> None:
    used_font = font(18, True)
    width = text_width(draw, label, used_font) + 34
    draw.rounded_rectangle((right - width, y, right, y + 36), radius=18, fill=fill)
    draw.text((right - width + 17, y + 7), label, font=used_font, fill=ink)


def status_card(draw: ImageDraw.ImageDraw, y: int, palette: Palette, index: int, label: str, detail: str, state: str) -> int:
    body_font = font(22)
    detail_lines = wrap(draw, detail, body_font, 542)
    height = max(150, 92 + len(detail_lines) * 38)
    card_fill = palette.surface if index % 2 == 0 else palette.card_tint
    draw.rounded_rectangle((52, y, WIDTH - 52, y + height), radius=22, fill=card_fill, outline=palette.border, width=2)
    color = palette.colors[index % len(palette.colors)]
    draw.rounded_rectangle((54, y + 22, 63, y + 61), radius=4, fill=color)
    draw.text((82, y + 22), label, font=font(26, True), fill=palette.ink)
    tag(draw, WIDTH - 76, y + 20, state, color, palette.ink)
    draw_lines(draw, 82, y + 70, detail_lines, body_font, palette.muted, gap=10)
    return y + height + 18


def small_note(draw: ImageDraw.ImageDraw, y: int, palette: Palette) -> int:
    draw.rounded_rectangle((52, y, WIDTH - 52, y + 108), radius=22, fill=palette.title_fill)
    draw.rounded_rectangle((73, y + 22, 82, y + 86), radius=4, fill=palette.accent)
    draw.text((100, y + 20), "数据说明", font=font(22, True), fill=palette.accent)
    draw.text((100, y + 54), "日程来源与机器人状态均保存于本地缓存\n刷新后自动更新，不丢失上次可用结果。", font=font(19), fill=palette.muted, spacing=4)
    return y + 132


def render(palette: Palette) -> Path:
    cards = (
        ("直播防护", "已开启。直播开始时，将自动关闭群内小游戏。", "已开启"),
        ("小游戏总开关", "当前允许使用小游戏。开播期间由直播防护临时接管。", "正常"),
        ("自动暂停", "当前没有自动恢复倒计时。\n需要时可在管理指令中设置。", "未设置"),
        ("日程缓存", "最近刷新：2026-07-28 01:54\n未来可识别直播：13 条。", "已刷新"),
    )
    image = Image.new("RGB", (WIDTH, 1160), palette.background)
    draw = ImageDraw.Draw(image)
    y = rounded_matrix_title(draw, palette)
    for index, (label, detail, state) in enumerate(cards):
        y = status_card(draw, y, palette, index, label, detail, state)
    y = small_note(draw, y, palette)
    footer = "本地确定性渲染  |  数据来自机器人本地数据库"
    draw.text(((WIDTH - text_width(draw, footer, font(16))) // 2, y), footer, font=font(16), fill=palette.muted)
    draw_corner_accents(draw, palette, y + 52)
    # Crop to the actual content while retaining a calm lower margin.
    image = image.crop((0, 0, WIDTH, y + 52))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{palette.name}.png"
    image.save(output, format="PNG", optimize=True)
    return output


if __name__ == "__main__":
    for current in PALETTES:
        print(render(current))
