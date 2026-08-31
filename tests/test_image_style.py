from PIL import Image

from bot.services.image_style import transparent_rounded_corners


def test_transparent_rounded_corners_preserves_existing_alpha():
    source = Image.new("RGBA", (100, 100), (240, 80, 145, 255))
    source.putpixel((50, 50), (240, 80, 145, 96))

    result = transparent_rounded_corners(source)

    assert result.mode == "RGBA"
    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((34, 34))[3] == 255
    assert result.getpixel((50, 50))[3] == 96
