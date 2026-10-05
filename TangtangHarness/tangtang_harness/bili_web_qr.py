"""Harness-owned WEB QR login that retains cookies from the poll response."""
from __future__ import annotations

from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

import qrcode
from bilibili_api import Credential, HEADERS, Picture, get_client
from bilibili_api.login_v2 import QrCodeLoginEvents


PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode/"


class WebQrLogin:
    def __init__(self):
        self._key = ""
        self._picture = None
        self._credential = None

    async def _request(self, endpoint: str, params: dict):
        try:
            response = await get_client().request(method="GET", url=PASSPORT + endpoint,
                params=params, headers=HEADERS, allow_redirects=False)
        except Exception as exc:
            raise RuntimeError(f"B站扫码请求失败（{type(exc).__name__}）。") from None
        if response.code != 200:
            raise RuntimeError(f"B站扫码请求返回 HTTP {response.code}。")
        payload = json.loads(response.raw)
        if payload["code"] != 0:
            raise RuntimeError(f"B站扫码接口返回错误码 {payload['code']}。")
        return response, payload["data"]

    async def generate_qrcode(self):
        _, data = await self._request("generate", {"source": "main-fe-header"})
        self._key = data["qrcode_key"]
        image = qrcode.make(data["url"])
        content = BytesIO()
        image.save(content, format="PNG")
        self._picture = Picture(height=image.height, width=image.width, imageType="png",
            size=len(content.getvalue()) / 1024, content=content.getvalue())

    def get_qrcode_picture(self):
        return self._picture

    def get_credential(self):
        if self._credential is None:
            raise RuntimeError("B站扫码登录尚未完成。")
        return self._credential

    async def check_state(self):
        response, data = await self._request("poll", {"qrcode_key": self._key, "source": "main-fe-header"})
        state = data["code"]
        pending = {86101: QrCodeLoginEvents.SCAN, 86090: QrCodeLoginEvents.CONF,
                   86038: QrCodeLoginEvents.TIMEOUT}
        if state in pending:
            return pending[state]
        if state != 0:
            raise RuntimeError(f"B站扫码登录返回错误码 {state}。")
        cookies = {key: values[0] for key, values in parse_qs(urlsplit(data.get("url", "")).query).items()}
        cookies.update({key: value for key, value in response.cookies.items() if value})
        if not cookies.get("SESSDATA") or not cookies.get("bili_jct"):
            raise RuntimeError("B站扫码确认后未返回完整登录 Cookie，请重新扫码。")
        self._credential = Credential(sessdata=cookies["SESSDATA"], bili_jct=cookies["bili_jct"],
            dedeuserid=cookies.get("DedeUserID"), buvid3=cookies.get("buvid3"), buvid4=cookies.get("buvid4"),
            ac_time_value=data.get("refresh_token") or cookies.get("ac_time_value"))
        return QrCodeLoginEvents.DONE
