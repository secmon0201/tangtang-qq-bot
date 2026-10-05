import asyncio
from contextlib import asynccontextmanager
import signal
import socket
from types import SimpleNamespace

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from tangtang_harness.public_gateway import create_public_gateway, public_gateway_lifespan


BASE_URL = "https://bot.example.invalid"
TOKEN = "A_" + "x" * 30


def main_app():
    calls = {"startup": 0, "shutdown": 0, "requests": []}

    @asynccontextmanager
    async def lifespan(app):
        calls["startup"] += 1
        yield
        calls["shutdown"] += 1

    app = FastAPI(lifespan=lifespan)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT"])
    async def echo(path: str, request: Request):
        calls["requests"].append((request.method, request.url.path))
        return {"method": request.method, "path": request.url.path,
                "query": dict(request.query_params), "body": (await request.body()).decode()}

    @app.websocket("/onebot/v11/ws")
    async def websocket(socket: WebSocket):
        await socket.accept()
        await socket.send_text("private endpoint")

    return app, calls


def store(**settings):
    values = {"web_base_url": BASE_URL, "public_gateway_port": 0, **settings}
    return SimpleNamespace(get_setting=lambda name, default=None: values.get(name, default))


def test_gateway_shares_business_routes_body_and_query_without_main_lifespan():
    app, calls = main_app()
    with TestClient(create_public_gateway(app, base_url=BASE_URL)) as client:
        params = {"group_id": "201", "group": "domain", "scope": "week"}
        for path in (
            "/business/ranking", "/business/ranking/api", "/business/help",
            "/business/help/api", "/business/schedule", "/business/asoul",
            "/business/schedule/api/schedule", "/business/duplicate", "/business/whitelist",
            "/business/announcement", "/business/assets/tangtang-avatar.jpg",
            "/business/assets/tangtang_avatar.jpg",
        ):
            response = client.get(path, params=params)
            assert response.status_code == 200
            assert response.json()["query"] == params
        assert client.head("/business/help").status_code == 200
        for name in ("duplicate", "whitelist", "announcement"):
            assert client.get(f"/business/{name}/api/{TOKEN}/state").status_code == 200
        actions = {
            "duplicate": ("scan", "whitelist/add", "whitelist/remove"),
            "whitelist": ("scan", "whitelist/add", "whitelist/remove"),
            "announcement": ("preview", "graphic-preview", "send-preview", "send-image"),
        }
        for name, paths in actions.items():
            for action in paths:
                response = client.post(f"/business/{name}/api/{TOKEN}/{action}", content=b"synthetic body")
                assert response.status_code == 200
                assert response.json()["body"] == "synthetic body"
    assert calls["startup"] == calls["shutdown"] == 0


def test_gateway_blocks_private_unknown_paths_methods_and_invalid_tokens():
    app, calls = main_app()
    client = TestClient(create_public_gateway(app, base_url=BASE_URL))
    for path in (
        "/", "/api/status", "/api/settings", "/api/tools/run", "/api/analytics/overview",
        "/api/assets/digest", "/internal/codex/completion", "/onebot/v11/ws", "/docs",
        "/openapi.json", "/business/unknown", "/business/help/", "/business/assets/private.json",
        "/business/assets/%2e%2e/config/settings.json", "/business/ranking/api/other",
        f"/business/duplicate/api/{TOKEN}/preview", f"/business/announcement/api/{TOKEN}/scan",
        f"/business/unknown/api/{TOKEN}/state", f"/business/announcement/api/{TOKEN[:-1]}/state",
        f"/business/announcement/api/{TOKEN}x/state", "/business/announcement/api/invalid!/state",
    ):
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404
    for method, path in (
        ("POST", "/business/ranking"), ("PUT", "/business/help"), ("OPTIONS", "/business/help"),
        ("GET", f"/business/duplicate/api/{TOKEN}/scan"),
        ("POST", f"/business/duplicate/api/{TOKEN}/state"),
        ("DELETE", f"/business/announcement/api/{TOKEN}/send-image"),
    ):
        assert client.request(method, path).status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/onebot/v11/ws"):
            pass
    assert calls["requests"] == []
    client.close()


def test_short_host_redirects_and_legacy_public_links_preserve_views():
    app, calls = main_app()
    client = TestClient(create_public_gateway(app, base_url=BASE_URL, short_host="short.example.invalid"))
    for path, target in (("/r", "/business/schedule?view=week"), ("/h", "/business/help")):
        response = client.get(path, headers={"Host": "SHORT.EXAMPLE.INVALID:443"}, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == BASE_URL + target
        assert response.headers["cache-control"] == "no-store"
    for path in ("/", "/business/help", "/help/", "/unknown"):
        assert client.get(path, headers={"Host": "short.example.invalid"}).status_code == 404
    assert client.post("/r", headers={"Host": "short.example.invalid"}).status_code == 404
    for path, target in (("/help", "/business/help"), ("/help/", "/business/help"),
                         ("/live", "/business/schedule"), ("/live/", "/business/schedule")):
        response = client.get(path + "?view=tomorrow", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == BASE_URL + target + "?view=tomorrow"
    assert calls["requests"] == []
    client.close()


@pytest.mark.asyncio
async def test_gateway_lifespan_is_disabled_by_default(monkeypatch):
    app, calls = main_app()

    def unexpected_socket(*args):
        raise AssertionError("Disabled gateway must not bind a socket")

    monkeypatch.setattr("tangtang_harness.public_gateway.socket.socket", unexpected_socket)
    async with public_gateway_lifespan(app, store()) as server:
        assert server is None
    assert calls["startup"] == calls["shutdown"] == 0


@pytest.mark.asyncio
async def test_gateway_lifespan_owns_local_listener_and_releases_it():
    app, calls = main_app()
    loop = asyncio.get_running_loop()
    seen_loops = []

    async def same_loop(scope, receive, send):
        seen_loops.append(asyncio.get_running_loop())
        await app(scope, receive, send)

    original_signals = {item: signal.getsignal(item) for item in (signal.SIGINT, signal.SIGTERM)}
    async with public_gateway_lifespan(same_loop, store(public_gateway_enabled=True)) as server:
        assert server is not None and server.started
        port = server.config.port
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.get("/business/ranking?group_id=201&group=domain&scope=week")
            assert response.status_code == 200
            assert response.json()["query"] == {"group_id": "201", "group": "domain", "scope": "week"}
            assert (await client.get("/api/status")).status_code == 404
        assert {item: signal.getsignal(item) for item in original_signals} == original_signals
    assert seen_loops == [loop]
    assert calls["startup"] == calls["shutdown"] == 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))
    assert not any(task.get_name() == "harness-public-gateway" for task in asyncio.all_tasks())


@pytest.mark.asyncio
async def test_gateway_port_failure_keeps_main_business_app_available():
    app, calls = main_app()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        async with public_gateway_lifespan(
            app, store(public_gateway_enabled=True, public_gateway_port=occupied.getsockname()[1])
        ) as server:
            assert server is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.invalid") as client:
                assert (await client.get("/api/status")).status_code == 200
    assert calls["requests"] == [("GET", "/api/status")]


def test_core_login_routes_preserve_fixed_target_query_body_and_headers():
    app, calls = main_app()
    forwarded = []
    query = "source=qq%20page&keep=%2B&empty="

    def core(request):
        forwarded.append((request.method, request.url.path))
        assert request.url.scheme == "http"
        assert request.url.host == "127.0.0.1" and request.url.port == 8765
        assert request.url.query == query.encode()
        assert request.headers["host"] == "127.0.0.1:8765"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["cookie"] == "session=synthetic"
        assert request.headers["origin"] == BASE_URL
        assert request.content == (b'{"auth":"synthetic"}' if request.method == "POST" else b"")
        assert set(request.extensions["timeout"].values()) == {30}
        return httpx.Response(302, content=b"synthetic login response", headers=[
            ("Content-Type", "text/plain; charset=utf-8"), ("Location", "/nte/done?ok=1"),
            ("Set-Cookie", "session=first; HttpOnly"), ("Set-Cookie", "route=second; Secure"),
            ("Cache-Control", "no-store"),
        ])

    client = TestClient(create_public_gateway(app, base_url=BASE_URL, core_transport=httpx.MockTransport(core)))
    paths = [
        *(('GET', path) for path in (
            "/nte/i/synthetic-token", "/nte/status/synthetic-token", "/nte/done", "/waves/i/synthetic-token"
        )),
        *(('POST', path) for path in (
            "/nte/login", "/nte/sendSmsCode", "/nte/wanmei/prepare", "/nte/wanmei/sendSmsCode",
            "/nte/wanmei/login", "/nte/wanmei/selectRole", "/waves/login", "/waves/l/login",
            "/waves/l/bind", "/waves/c/sendCode", "/waves/c/login", "/waves/add_token",
        )),
    ]
    for method, path in paths:
        response = client.request(method, path + "?" + query,
            content=b'{"auth":"synthetic"}' if method == "POST" else b"",
            headers={"Content-Type": "application/json", "Cookie": "session=synthetic", "Origin": BASE_URL},
            follow_redirects=False)
        assert response.status_code == 302
        assert response.content == b"synthetic login response"
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["location"] == "/nte/done?ok=1"
        assert response.headers.get_list("set-cookie") == ["session=first; HttpOnly", "route=second; Secure"]
        assert response.headers["cache-control"] == "no-store"
    assert forwarded == paths
    assert calls["requests"] == []
    client.close()


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout])
def test_core_login_failure_returns_502(error):
    app, calls = main_app()

    def unavailable(request):
        raise error("synthetic upstream failure", request=request)

    client = TestClient(create_public_gateway(app, base_url=BASE_URL,
                        core_transport=httpx.MockTransport(unavailable)))
    response = client.get("/nte/i/synthetic-token")
    assert response.status_code == 502
    assert response.text == "Game login service unavailable"
    assert calls["requests"] == []
    client.close()


def test_core_private_paths_unknown_login_paths_and_wrong_methods_are_blocked():
    app, calls = main_app()

    def unexpected_core(request):
        raise AssertionError("Rejected routes must never contact Core")

    client = TestClient(create_public_gateway(app, base_url=BASE_URL, short_host="short.example.invalid",
                        core_transport=httpx.MockTransport(unexpected_core)))
    for path in (
        "/api/send_msg", "/ws/QQLocalDataBot", "/nte", "/nte/i/", "/nte/status/",
        "/nte/other", "/nte/login/other", "/nte/wanmei/unknown", "/nte/wanmei/login/other",
        "/nte/i/synthetic/extra", "/nte/i/%2e%2e", "/nte/i/%2e%2e%2fapi%2fsend_msg",
        "/waves", "/waves/i/", "/waves/i/synthetic/extra", "/waves/get", "/waves/token",
        "/waves/fonts/fonts.css", "/waves/gacha/synthetic", "/waves/panel-edit/",
        "/waves/l/unknown", "/waves/c/unknown", "/waves/login/other",
    ):
        for method in ("GET", "POST", "HEAD"):
            assert client.request(method, path).status_code == 404
    for method, path in (
        ("GET", "/nte/login"), ("GET", "/nte/wanmei/prepare"), ("POST", "/nte/done"),
        ("POST", "/nte/i/synthetic"), ("POST", "/nte/status/synthetic"),
        ("HEAD", "/nte/i/synthetic"), ("GET", "/waves/login"), ("POST", "/waves/i/synthetic"),
        ("PUT", "/waves/l/login"),
    ):
        assert client.request(method, path).status_code == 404
    assert client.get("/nte/i/synthetic", headers={"Host": "short.example.invalid"}).status_code == 404
    assert calls["requests"] == []
    client.close()
