from pathlib import Path
from io import BytesIO

from PIL import Image

from bot.services import global_announcement_web as web


def test_announcement_web_session_is_single_use():
    sessions = web.AnnouncementWebSessions()
    session = sessions.create(100)

    assert sessions.get(session.token) is session
    assert sessions.begin_send(session.token) is session
    sessions.finish_send(session.token)

    try:
        sessions.begin_send(session.token)
    except ValueError as exc:
        assert "已提交" in str(exc)
    else:
        raise AssertionError("a delivered session must not send again")


def test_announcement_web_session_cleanup_removes_uploaded_file(tmp_path):
    sessions = web.AnnouncementWebSessions()
    session = sessions.create(100)
    upload = tmp_path / "upload.png"
    upload.write_bytes(b"test")
    session.upload_path = upload
    session.expires_at = 0

    sessions.cleanup()

    assert sessions.get(session.token) is None
    assert not upload.exists()


def test_announcement_web_base_url_requires_https(monkeypatch, tmp_path):
    path = Path(tmp_path / "tunnel_url.txt")
    monkeypatch.setattr(web, "TUNNEL_URL_PATH", path)

    path.write_text("http://127.0.0.1:8080", encoding="utf-8")
    assert web.announcement_web_base_url() is None

    path.write_text("https://example.trycloudflare.com/\n", encoding="utf-8")
    assert web.announcement_web_base_url() == "https://example.trycloudflare.com"


def test_announcement_web_preview_is_small_webp_copy(tmp_path):
    poster = tmp_path / "poster.png"
    Image.new("RGBA", (2160, 1080), (240, 80, 145, 255)).save(poster)

    preview = web.announcement_web_preview_bytes(poster)

    assert preview[:4] == b"RIFF"
    assert len(preview) < poster.stat().st_size
    with Image.open(BytesIO(preview)) as image:
        assert image.format == "WEBP"
        assert image.width <= web.WEB_PREVIEW_MAX_WIDTH


def test_announcement_web_uses_the_preview_post_response_as_the_image():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert "const previewRequest=" in page
    assert "await r.blob()" in page
    assert "URL.createObjectURL(image)" in page
    assert "src=\"${api}/preview" not in page


def test_announcement_web_shows_a_pending_state_while_generating_a_preview():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert "const setPreviewBusy=" in page
    assert "正在生成预览，请稍候…" in page
    assert "正在生成预览…" in page
    assert "state.previewPending" in page


def test_announcement_web_sends_original_image_once_with_upload_progress():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert "const uploadRequest=" in page
    assert "正在上传原图" in page
    assert "正在发送原图" in page
    assert "原图不会生成或回传预览" in page
    assert 'alt="原图预览"' not in page


def test_announcement_proxy_streams_instead_of_buffering_complete_files():
    proxy = (Path(__file__).parents[1] / "scripts" / "global_announcement_web_proxy.py").read_text(encoding="utf-8")

    assert "COPY_CHUNK_BYTES = 64 * 1024" in proxy
    assert "while remaining:" in proxy
    assert "response.read(COPY_CHUNK_BYTES)" in proxy
    assert "data = response.read()" not in proxy


def test_announcement_web_hides_graphic_layout_implementation_details():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert "图片按海报固定宽度等比缩放" not in page


def test_announcement_web_uses_the_public_notice_path():
    plugin = (Path(__file__).parents[1] / "bot" / "plugins" / "global_announcement.py").read_text(encoding="utf-8")
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert 'f"{base_url}/notice/{session.token}"' in plugin
    assert "area=segments[0]==='notice'?'notice':'announcement'" in page


def test_announcement_web_submits_optional_text_for_all_three_modes():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert 'id="textExtra"' in page
    assert 'id="graphicExtra"' in page
    assert 'id="imageExtra"' in page
    assert "extra_text:$('textExtra').value.trim()" in page
    assert "form.append('extra_text',$('graphicExtra').value.trim())" in page
    assert "form.append('extra_text',$('imageExtra').value.trim())" in page


def test_announcement_web_sends_a_non_empty_json_body_when_confirming_a_poster():
    page = (Path(__file__).parents[1] / "bot" / "resources" / "global_announcement_web.html").read_text(encoding="utf-8")

    assert "request('/send-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})" in page
