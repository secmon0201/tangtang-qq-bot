"""Expose approved business pages through the existing local tunnel listener."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import re
import socket
from urllib.parse import urlsplit

import httpx
from loguru import logger
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn


READ_PATHS = frozenset({
    *(f"/business/{name}" for name in (
        "help", "ranking", "schedule", "asoul", "duplicate", "whitelist", "announcement"
    )),
    "/business/help/api", "/business/ranking/api", "/business/schedule/api/schedule",
    "/business/assets/tangtang-avatar.jpg", "/business/assets/tangtang_avatar.jpg",
})
STATE_PATH = re.compile(
    r"/business/(?:duplicate|whitelist|announcement)/api/[A-Za-z0-9_-]{32}/state"
)
ACTION_PATH = re.compile(
    r"/business/(?:(?:duplicate|whitelist)/api/[A-Za-z0-9_-]{32}/"
    r"(?:scan|whitelist/(?:add|remove))|announcement/api/[A-Za-z0-9_-]{32}/"
    r"(?:preview|graphic-preview|send-preview|send-image))"
)
CORE_GET_PATH = re.compile(r"/(?:nte/(?:i|status)|waves/i)/[A-Za-z0-9_-]+")
CORE_POST_PATHS = frozenset({
    "/nte/login", "/nte/sendSmsCode", "/nte/wanmei/prepare", "/nte/wanmei/sendSmsCode",
    "/nte/wanmei/login", "/nte/wanmei/selectRole", "/waves/login", "/waves/l/login",
    "/waves/l/bind", "/waves/c/sendCode", "/waves/c/login", "/waves/add_token",
})
CORE_REQUEST_HEADERS = frozenset({
    b"content-type", b"accept", b"accept-language", b"origin", b"referer", b"user-agent", b"cookie",
})
CORE_RESPONSE_HEADERS = frozenset({b"content-type", b"location", b"set-cookie", b"cache-control"})


async def _forward_core(scope: Scope, receive: Receive, send: Send,
                        transport: httpx.AsyncBaseTransport | None) -> None:
    """Forward only the selected upstream login endpoints to the local Core."""
    path = scope.get("raw_path", scope["path"].encode("ascii"))
    query = scope.get("query_string", b"")
    url = httpx.URL("http://127.0.0.1:8765").copy_with(raw_path=path + (b"?" + query if query else b""))
    headers = [(name, value) for name, value in scope["headers"] if name.lower() in CORE_REQUEST_HEADERS]
    try:
        async with asyncio.timeout(30):
            body = await Request(scope, receive).body()
            async with httpx.AsyncClient(timeout=30, trust_env=False, transport=transport,
                                         follow_redirects=False) as client:
                upstream = await client.request(scope["method"], url, content=body, headers=headers)
        response = Response(upstream.content, status_code=upstream.status_code)
        response.raw_headers.extend((name, value) for name, value in upstream.headers.raw
                                    if name.lower() in CORE_RESPONSE_HEADERS)
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.warning("上游游戏登录服务不可用：{}", type(exc).__name__)
        response = Response("Game login service unavailable", status_code=502)
    await response(scope, receive, send)


def create_public_gateway(app: ASGIApp, *, base_url: str, short_host: str = "",
                          core_transport: httpx.AsyncBaseTransport | None = None) -> ASGIApp:
    """Keep console endpoints private while sharing the main application's runtime."""
    base_url = base_url.strip().rstrip("/")
    origin = urlsplit(base_url)
    if (origin.scheme != "https" or not origin.hostname or origin.username
            or origin.path or origin.query or origin.fragment):
        raise ValueError("公开网页地址必须是 HTTPS 域名入口。")
    short_host = short_host.strip().lower().rstrip(".")

    async def gateway(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            return

        path, method = scope["path"], scope["method"]
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1").partition(":")[0].lower().rstrip(".")
        if short_host and host == short_host:
            target = {"/r": "/business/schedule?view=week", "/h": "/business/help"}.get(path)
            if method in {"GET", "HEAD"} and target:
                await RedirectResponse(base_url + target, status_code=302,
                                       headers={"Cache-Control": "no-store"})(scope, receive, send)
            else:
                await Response("Not Found", status_code=404)(scope, receive, send)
            return

        core_allowed = (
            method == "GET" and (path == "/nte/done" or CORE_GET_PATH.fullmatch(path))
            or method == "POST" and path in CORE_POST_PATHS
        )
        if core_allowed:
            await _forward_core(scope, receive, send, core_transport)
            return

        if method in {"GET", "HEAD"}:
            target = {"/help": "/business/help", "/help/": "/business/help",
                      "/live": "/business/schedule", "/live/": "/business/schedule"}.get(path)
            if target:
                query = scope.get("query_string", b"").decode("latin-1")
                location = base_url + target + ("?" + query if query else "")
                await RedirectResponse(location, status_code=302,
                                       headers={"Cache-Control": "no-store"})(scope, receive, send)
                return
            allowed = path in READ_PATHS or STATE_PATH.fullmatch(path)
        else:
            allowed = method == "POST" and ACTION_PATH.fullmatch(path)
        if not allowed:
            await Response("Not Found", status_code=404)(scope, receive, send)
            return
        await app(scope, receive, send)

    return gateway


class GatewayServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        """Leave process shutdown signals with the main Harness server."""
        yield


async def _stop_gateway(server: GatewayServer | None, task: asyncio.Task | None,
                        listener: socket.socket | None) -> None:
    if server is not None:
        server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except Exception as exc:
            logger.error("公开业务网关停止失败：{}", exc)
    if listener is not None:
        listener.close()


@asynccontextmanager
async def public_gateway_lifespan(app: ASGIApp, store):
    """Own a second listener on the main loop without starting another runtime."""
    if not store.get_setting("public_gateway_enabled", False):
        yield None
        return

    server = task = listener = None
    try:
        gateway = create_public_gateway(
            app, base_url=str(store.get_setting("web_base_url", "")),
            short_host=str(store.get_setting("public_short_host", "")),
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", int(store.get_setting("public_gateway_port", 18769))))
        listener.listen(128)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = GatewayServer(uvicorn.Config(
            gateway, host="127.0.0.1", port=port, lifespan="off", interface="asgi3",
            access_log=False, log_config=None, timeout_graceful_shutdown=4,
        ))
        task = asyncio.create_task(server.serve(sockets=[listener]), name="harness-public-gateway")

        async def wait_started():
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("公开业务网关未进入监听状态。")
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_started(), timeout=2)
        logger.info("公开业务网关已监听 127.0.0.1:{}", port)
    except Exception as exc:
        logger.error("公开业务网关启动失败，Harness 继续运行：{}", exc)
        await _stop_gateway(server, task, listener)
        server = task = listener = None

    try:
        yield server
    finally:
        await _stop_gateway(server, task, listener)
