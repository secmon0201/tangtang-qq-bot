import asyncio
from datetime import datetime
import json
import re

from fastapi.testclient import TestClient
import pytest

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig
from tangtang_harness.runtime import Runtime


@pytest.fixture
def ranking_runtime(tmp_path, monkeypatch):
    runtime = Runtime(HarnessConfig(root=tmp_path, mode="observe", background_enabled=False))
    runtime.store.set_setting("avatar_fetch_enabled", False)
    tools = runtime.tools
    for group_id, name in ((199, "域外群"), (201, "群甲"), (202, "群乙")):
        tools.domains.ensure_group(group_id, group_name=name)
    domain = tools.domains.create_cluster("合成集群")
    for group_id in (201, 202):
        tools.domains.add_group_to_cluster(group_id, domain.domain_id)
    monkeypatch.setattr(tools.stats, "local_now", lambda: datetime(2026, 10, 8, 12))
    counts = {
        201: (("2026-10-08", 2), ("2026-10-05", 3), ("2026-10-01", 5), ("2026-09-20", 7)),
        202: (("2026-10-08", 1), ("2026-10-06", 4), ("2026-10-02", 6), ("2026-09-19", 8)),
        199: (("2026-10-08", 20),),
    }
    for group_id, days in counts.items():
        for day, count in days:
            for index in range(count):
                tools.db.record_message(f"{group_id}:{day}:{index}", group_id, group_id + 1000,
                                        f"成员{group_id}", datetime.fromisoformat(day + "T12:00:00"))
    yield runtime
    asyncio.run(runtime.close())


def embedded_payload(source):
    return json.loads(re.search(r"const embedded = (.*?);\s*const root", source, re.S).group(1))


@pytest.mark.parametrize("scope,current_total,domain_total", [
    ("day", 2, 3), ("week", 5, 10), ("month", 10, 21), ("total", 17, 36),
])
def test_ranking_page_and_api_share_current_group_domain_and_period(
    ranking_runtime, scope, current_total, domain_total
):
    client = TestClient(create_app(runtime=ranking_runtime))
    try:
        for group, expected in (("current", current_total), ("domain", domain_total)):
            params = {"group_id": 201, "group": group, "scope": scope}
            response = client.get("/business/ranking/api", params=params)
            assert response.status_code == 200
            data = response.json()
            page = client.get("/business/ranking", params=params)
            assert page.status_code == 200
            assert embedded_payload(page.text) == data
            assert data["scope"] == scope
            assert data["message_total"] == expected
            assert data["anchor_group_id"] == 201
            assert {option["key"] for option in data["group_options"]} == {"domain", "201", "202"}
            assert not any(row["nickname"] == "成员199" for row in data["rows"])
            if group == "domain":
                assert data["group"] == "domain"
                assert data["group_label"] == "合成集群"
                assert data["chart"]["kind"] == "group"
                assert sum(row["message_count"] for row in data["chart"]["rows"]) == expected
            else:
                assert data["group"] == "201"
                assert data["group_label"] == "群甲"
                assert data["chart"]["kind"] == "daily"
        assert not ranking_runtime.store.requests()
    finally:
        client.close()


def test_ranking_member_selection_preserves_the_current_domain_anchor(ranking_runtime):
    client = TestClient(create_app(runtime=ranking_runtime))
    try:
        params = {"group_id": 201, "group": "202", "scope": "total"}
        member = client.get("/business/ranking/api", params=params).json()
        assert member["message_total"] == 19
        assert member["group"] == "202"
        assert member["anchor_group_id"] == 201
        assert member["group_label"] == "群乙"
        assert {option["key"] for option in member["group_options"]} == {"domain", "201", "202"}
        page = client.get("/business/ranking", params=params)
        assert embedded_payload(page.text) == member
        assert "&group_id=${encodeURIComponent(rankingGroupId)}" in page.text
        assert "next.searchParams.set('group_id', data.anchor_group_id)" in page.text
        assert "next.searchParams.delete('cluster')" in page.text
        returned = client.get("/business/ranking/api", params={**params, "group": "domain"}).json()
        assert returned["message_total"] == 36
        assert returned["group_label"] == "合成集群"
        outside = client.get("/business/ranking/api", params={**params, "group": "199"})
        assert outside.status_code == 400
        assert "当前群域" in outside.json()["detail"]
    finally:
        client.close()


def test_ranking_period_and_cluster_aliases_and_default_current_view(ranking_runtime):
    client = TestClient(create_app(runtime=ranking_runtime))
    try:
        current = client.get("/business/ranking/api", params={"group_id": 201}).json()
        assert current["scope"] == "day"
        assert current["message_total"] == 2
        aliases = {"group_id": 201, "period": "week", "cluster": "true"}
        clustered = client.get("/business/ranking/api", params=aliases).json()
        assert clustered["scope"] == "week"
        assert clustered["message_total"] == 10
        page = client.get("/business/ranking", params=aliases)
        assert embedded_payload(page.text) == clustered
        explicit = client.get("/business/ranking/api", params={**aliases, "group": "202", "scope": "month"}).json()
        assert explicit["scope"] == "month"
        assert explicit["group"] == "202"
        assert explicit["message_total"] == 11
        assert client.get("/business/ranking/api", params={"group_id": 201, "scope": "annual"}).status_code == 400
    finally:
        client.close()
