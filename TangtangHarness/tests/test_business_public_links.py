import asyncio
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig
from tangtang_harness.public_gateway import create_public_gateway
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent, ToolCall


@pytest.fixture
def public_runtime(tmp_path):
    runtime = Runtime(HarnessConfig(root=tmp_path, background_enabled=False))
    runtime.store.set_setting("operator_ids", [101])
    runtime.store.set_setting("web_base_url", "https://bot.example.invalid/")
    runtime.store.set_setting("avatar_fetch_enabled", False)
    runtime.tools.domains.ensure_group(201, group_name="合成群")
    yield runtime
    asyncio.run(runtime.close())


def public_client(runtime):
    return TestClient(create_public_gateway(
        create_app(runtime=runtime), base_url="https://bot.example.invalid"
    ))


def operator_event(user_id=101):
    return InboundEvent("synthetic-operator", 999, user_id, None, "管理网页")


@pytest.mark.parametrize("name", ["duplicate", "whitelist", "announcement"])
def test_public_management_link_uses_qq_authorized_session(public_runtime, name):
    tools = public_runtime.tools
    client = public_client(public_runtime)
    for query in ("", "?user_id=101", "?token=invalid"):
        assert client.get(f"/business/{name}{query}").status_code == 404
    assert not tools.web.sessions

    denied = asyncio.run(tools.execute(operator_event(102), ToolCall("operator_web", {"page": name})))
    assert denied.status == "denied" and not tools.web.sessions
    result = asyncio.run(tools.execute(operator_event(), ToolCall("operator_web", {"page": name})))
    assert result.status == "ok"
    url = urlsplit(result.data["panel"])
    assert url.scheme == "https" and url.netloc == "bot.example.invalid"
    token = parse_qs(url.query)["token"][0]
    page = client.get(url.path + "?" + url.query)
    assert page.status_code == 200
    assert f"/business/{name}/api/{token}" in page.text
    state = client.get(f"/business/{name}/api/{token}/state")
    assert state.status_code == 200 and state.json()["actor_id"] == 101
    other = "announcement" if name == "duplicate" else "duplicate"
    assert client.get(f"/business/{other}?token={token}").status_code == 404
    assert client.get(url.path + "?" + url.query).status_code == 200

    tools.web.sessions[token]["expires"] = 0
    assert client.get(url.path + "?" + url.query).status_code == 404


def test_public_management_session_rechecks_operator_permissions(public_runtime):
    tools = public_runtime.tools
    result = asyncio.run(tools.execute(operator_event(), ToolCall("operator_web", {"page": "duplicate"})))
    url = urlsplit(result.data["panel"])
    token = parse_qs(url.query)["token"][0]
    tools.store.set_setting("operator_ids", [])
    client = public_client(public_runtime)
    assert client.get(url.path + "?" + url.query).status_code == 404
    assert client.get(f"/business/duplicate/api/{token}/state").status_code == 400


def test_management_link_is_not_disclosed_to_group_members(public_runtime):
    result = asyncio.run(public_runtime.tools.execute(
        InboundEvent("synthetic-group-operator", 999, 101, 201, "查重网页"),
        ToolCall("operator_web", {"page": "duplicate"}),
    ))
    assert result.status == "clarification" and "私聊" in result.text
    assert "panel" not in result.data and not public_runtime.tools.web.sessions


def test_public_announcement_rejects_upload_before_writing_without_session(public_runtime, monkeypatch):
    def unexpected_upload(*args):
        raise AssertionError("Unauthorized uploads must not reach business storage")

    monkeypatch.setattr(public_runtime.tools.web, "upload", unexpected_upload)
    client = public_client(public_runtime)
    response = client.post(
        "/business/announcement/api/" + "A" * 32 + "/graphic-preview",
        files={"image": ("synthetic.png", b"synthetic image", "image/png")},
    )
    assert response.status_code == 400
    assert not (public_runtime.config.root / "runtime" / "announcement-uploads").exists()


def test_public_ranking_preserves_group_domain_and_scope(public_runtime):
    client = public_client(public_runtime)
    params = {"group_id": 201, "group": "domain", "scope": "week"}
    page = client.get("/business/ranking", params=params)
    data = client.get("/business/ranking/api", params=params)
    assert page.status_code == data.status_code == 200
    assert data.json()["anchor_group_id"] == 201
    assert data.json()["group"] == "domain" and data.json()["scope"] == "week"
    assert "`/business/${kind}/api`" in page.text
    help_page = client.get("/business/help")
    assert help_page.status_code == 200 and "`/business/${kind}/api`" in help_page.text
    assert client.get("/business/assets/tangtang-avatar.jpg").status_code == 200
