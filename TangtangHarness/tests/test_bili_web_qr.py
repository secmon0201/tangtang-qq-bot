import asyncio
from collections import deque
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest
from bilibili_api import Credential
from bilibili_api.login_v2 import QrCodeLoginEvents
from bilibili_api.utils.network import BiliAPIResponse

from tangtang_harness import bili_web_qr
from tangtang_harness.business.asoul import ASoulService
from tangtang_harness.business.db import Database


class Client:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.requests = []

    async def request(self, **kwargs):
        self.requests.append(kwargs)
        data, cookies = self.replies.popleft()
        return BiliAPIResponse(200, {}, cookies, json.dumps({"code": 0, "data": data}).encode(), "https://example.invalid/")


def test_web_qr_keeps_response_cookies_and_state_progression(monkeypatch, tmp_path):
    client = Client([
        ({"qrcode_key": "synthetic-key", "url": "https://example.invalid/qr"}, {}),
        ({"code": 86101}, {}), ({"code": 86090}, {}),
        ({"code": 0, "url": "https://example.invalid/callback?SESSDATA=stale-cookie",
          "refresh_token": "synthetic-refresh"},
         {"SESSDATA": "synthetic-cookie", "bili_jct": "synthetic-csrf", "DedeUserID": "101", "buvid3": "synthetic-device"}),
    ])
    monkeypatch.setattr(bili_web_qr, "get_client", lambda: client)

    async def run():
        service = ASoulService(Database(tmp_path / "business.db"))
        login = await service.create_qr_login()
        with Image.open(BytesIO(login.get_qrcode_picture().content)) as image:
            assert image.format == "PNG" and image.width > 100
        assert await login.check_state() == QrCodeLoginEvents.SCAN
        assert await login.check_state() == QrCodeLoginEvents.CONF
        assert await service.wait_for_qr_login(login) is True
        cookies = login.get_credential().get_cookies()
        assert cookies["SESSDATA"] == "synthetic-cookie"
        assert cookies["bili_jct"] == "synthetic-csrf"
        assert cookies["DedeUserID"] == "101"
        assert cookies["buvid3"] == "synthetic-device"
        assert cookies["ac_time_value"] == "synthetic-refresh"
        assert service._credential().sessdata == "synthetic-cookie"
    asyncio.run(run())
    assert client.requests[-1]["params"]["qrcode_key"] == "synthetic-key"
    assert client.requests[-1]["allow_redirects"] is False


def test_web_qr_supports_legacy_url_and_expiration(monkeypatch):
    client = Client([
        ({"code": 86038}, {}),
        ({"code": 0, "url": "https://example.invalid/?SESSDATA=synthetic%2Ccookie&bili_jct=synthetic-csrf&DedeUserID=101"}, {}),
    ])
    monkeypatch.setattr(bili_web_qr, "get_client", lambda: client)

    async def run():
        login = bili_web_qr.WebQrLogin()
        assert await login.check_state() == QrCodeLoginEvents.TIMEOUT
        assert await login.check_state() == QrCodeLoginEvents.DONE
        assert login.get_credential().get_cookies()["SESSDATA"] == "synthetic%2Ccookie"
        assert login.get_credential().bili_jct == "synthetic-csrf"
    asyncio.run(run())


@pytest.mark.parametrize("cookies", [{}, {"SESSDATA": "synthetic-cookie"}, {"bili_jct": "synthetic-csrf"}])
def test_web_qr_rejects_incomplete_success(monkeypatch, cookies):
    client = Client([({"code": 0, "url": "https://example.invalid/"}, cookies)])
    monkeypatch.setattr(bili_web_qr, "get_client", lambda: client)
    login = bili_web_qr.WebQrLogin()
    with pytest.raises(RuntimeError, match="完整登录 Cookie"):
        asyncio.run(login.check_state())
    with pytest.raises(RuntimeError, match="尚未完成"):
        login.get_credential()


def test_web_qr_rejects_unknown_state_and_hides_request_secrets(monkeypatch):
    client = Client([({"code": 99999, "url": "https://example.invalid/?SESSDATA=synthetic-secret"}, {})])
    monkeypatch.setattr(bili_web_qr, "get_client", lambda: client)
    with pytest.raises(RuntimeError, match="错误码 99999") as error:
        asyncio.run(bili_web_qr.WebQrLogin().check_state())
    assert "synthetic-secret" not in str(error.value)

    class FailedClient:
        async def request(self, **kwargs):
            raise ValueError("qrcode_key=synthetic-secret")
    monkeypatch.setattr(bili_web_qr, "get_client", lambda: FailedClient())
    with pytest.raises(RuntimeError, match="ValueError") as error:
        asyncio.run(bili_web_qr.WebQrLogin().check_state())
    assert "synthetic-secret" not in str(error.value)


def test_qr_wait_does_not_replace_saved_credential_with_empty_success(tmp_path):
    service = ASoulService(Database(tmp_path / "business.db"))
    service.save_credential({"SESSDATA": "existing-cookie", "bili_jct": "existing-csrf"})

    async def done():
        return QrCodeLoginEvents.DONE
    login = SimpleNamespace(check_state=done, get_credential=lambda: Credential(sessdata="", bili_jct=""))
    with pytest.raises(RuntimeError, match="完整登录 Cookie"):
        asyncio.run(service.wait_for_qr_login(login))
    assert service._credential().sessdata == "existing-cookie"
