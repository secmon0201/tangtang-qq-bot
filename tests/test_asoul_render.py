from io import BytesIO

from PIL import Image

from bot.services.asoul_render import ASoulImageRenderer


class _ImageResponse:
    def __init__(self) -> None:
        buffer = BytesIO()
        Image.new("RGBA", (4, 4), "#ef5f8d").save(buffer, format="PNG")
        self.payload = buffer.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_remote_bilibili_image_retries_with_headers_and_caches(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.headers, timeout))
        if len(calls) == 1:
            raise OSError("temporary CDN failure")
        return _ImageResponse()

    monkeypatch.setattr("bot.services.asoul_render.urlopen", fake_urlopen)
    renderer = ASoulImageRenderer(tmp_path)

    first = renderer._remote_image("http://i0.hdslb.com/test.png")
    second = renderer._remote_image("http://i0.hdslb.com/test.png")

    assert first is not None and first.size == (4, 4)
    assert second is not None and second.size == (4, 4)
    assert len(calls) == 2
    assert calls[0][0].startswith("http://")
    assert calls[0][1]["Referer"] == "https://www.bilibili.com/"
    assert calls[0][2] == 10
