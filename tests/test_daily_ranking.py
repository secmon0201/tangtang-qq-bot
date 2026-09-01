from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.db import Database
import bot.services.daily_ranking as delivery_module
from bot.services.daily_ranking import (
    DELIVERY_TYPE,
    DomainDailyRankingDeliveryService,
)
from bot.services.group_domains import GroupDomainService


ZONE = ZoneInfo("Asia/Shanghai")


class _Stats:
    def __init__(self) -> None:
        self.requested_groups: list[tuple[int, ...]] = []

    def ranking_rows_for_groups(self, scope: str, group_ids):
        assert scope == "day"
        groups = tuple(group_ids)
        self.requested_groups.append(groups)
        return [{"rank": 1, "user_id": 7, "nickname": "成员", "message_count": 12}]

    def group_totals_for_groups(self, scope: str, group_ids):
        assert scope == "day"
        return [
            {
                "group_id": group_id,
                "group_name": f"group {group_id}",
                "message_count": 8 if index == 0 else 4,
            }
            for index, group_id in enumerate(group_ids)
        ]

    def render_rows(self, rows, title: str) -> str:
        return f"{title}: text fallback"


class _Avatars:
    async def prefetch(self, rows):
        return {}


class _Renderer:
    def __init__(self) -> None:
        self.calls = []

    def render_ranking(self, *args, **kwargs) -> Path:
        self.calls.append((args, kwargs))
        return Path("ranking.png")


class _CommunityRenderer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.payloads = []

    async def render_ranking(self, payload) -> Path:
        self.payloads.append(payload)
        if self.fail:
            raise RuntimeError("browser unavailable")
        return Path("community-ranking.png")


def _cluster_service(tmp_path, *, fail_html: bool = False):
    database = Database(tmp_path / "bot.db")
    database.seed_groups((1001, 1002))
    domains = GroupDomainService(database)
    domains.bootstrap()
    cluster = domains.create_cluster("测试集群")
    domains.add_group_to_cluster(1001, cluster.domain_id)
    domains.add_group_to_cluster(1002, cluster.domain_id)
    stats = _Stats()
    renderer = _Renderer()
    community_renderer = _CommunityRenderer(fail=fail_html)
    service = DomainDailyRankingDeliveryService(
        database,
        stats,
        domains,
        _Avatars(),
        renderer,
        community_renderer=community_renderer,
    )
    return database, domains, stats, renderer, community_renderer, service


def test_cluster_delivery_posts_one_shared_image_and_is_idempotent(monkeypatch, tmp_path):
    database, domains, stats, renderer, community_renderer, service = _cluster_service(tmp_path)
    sent = []

    async def send(bot, action, **params):
        sent.append((action, params))

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(
        delivery_module,
        "public_domain_ranking_url",
        lambda token: f"https://tangtang.secmon.cn/ranking/{token}/",
    )
    monkeypatch.setattr(delivery_module, "call_qq_action", send)
    now = datetime(2026, 7, 28, 23, 50, tzinfo=ZONE)

    result = asyncio.run(service.deliver_once(object(), now))
    repeated = asyncio.run(service.deliver_once(object(), now))

    assert result == {"status": "processed", "sent": 2, "failed": 0, "uncertain": 0}
    assert repeated == {"status": "already_delivered", "sent": 0, "failed": 0}
    assert [params["group_id"] for _, params in sent] == [1001, 1002]
    assert len({str(params["message"]) for _, params in sent}) == 1
    current_domain = domains.domain_for_group(1001)
    assert current_domain is not None
    assert str(sent[0][1]["message"]) == (
        "image:community-ranking.png\n"
        f"在线：https://tangtang.secmon.cn/ranking/{current_domain.public_token}/"
    )
    assert renderer.calls == []
    assert stats.requested_groups == [(1001, 1002)]
    assert community_renderer.payloads[0]["group"] == "domain"
    assert community_renderer.payloads[0]["chart"]["kind"] == "group"
    assert [
        row["status"] for row in database.ranking_deliveries(now.date(), DELIVERY_TYPE)
    ] == ["sent", "sent"]


def test_delivery_retries_only_failed_groups_during_catchup_window(monkeypatch, tmp_path):
    database, _domains, _stats, _renderer, _community_renderer, service = _cluster_service(tmp_path)
    attempts = []

    async def send(bot, action, **params):
        group_id = params["group_id"]
        attempts.append(group_id)
        if group_id == 1002 and attempts.count(group_id) == 1:
            raise RuntimeError("temporary disconnect")

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(delivery_module, "call_qq_action", send)
    now = datetime(2026, 7, 29, 0, 5, tzinfo=ZONE)

    first = asyncio.run(service.deliver_once(object(), now))
    second = asyncio.run(service.deliver_once(object(), now))

    assert first == {"status": "processed", "sent": 1, "failed": 1, "uncertain": 0}
    assert second == {"status": "processed", "sent": 1, "failed": 0, "uncertain": 0}
    assert attempts == [1001, 1002, 1002]
    delivery_day = datetime(2026, 7, 28, tzinfo=ZONE).date()
    assert [
        row["status"] for row in database.ranking_deliveries(delivery_day, DELIVERY_TYPE)
    ] == ["sent", "sent"]


def test_solo_delivery_is_opt_in_and_only_reads_its_own_group(monkeypatch, tmp_path):
    database = Database(tmp_path / "bot.db")
    domains = GroupDomainService(database)
    domains.ensure_group(9001, group_name="外部测试群", joined_at="2026-08-29T10:00:00+08:00")
    assert not domains.feature_enabled(9001, "speech_ranking_push")
    domains.set_feature(9001, "speech_ranking_push", True)
    stats = _Stats()
    community_renderer = _CommunityRenderer()
    service = DomainDailyRankingDeliveryService(
        database,
        stats,
        domains,
        _Avatars(),
        _Renderer(),
        community_renderer=community_renderer,
    )
    sent = []

    async def send(bot, action, **params):
        sent.append(params)

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(delivery_module, "call_qq_action", send)

    result = asyncio.run(
        service.deliver_once(object(), datetime(2026, 8, 29, 23, 50, tzinfo=ZONE))
    )

    assert result["sent"] == 1
    assert [params["group_id"] for params in sent] == [9001]
    assert stats.requested_groups == [(9001,)]
    payload = community_renderer.payloads[0]
    assert payload["chart"] is None
    assert payload["subtitle"].endswith("历史数据最早自 2026-08-29")


def test_delivery_does_not_create_work_outside_late_night_window(tmp_path):
    database, _domains, _stats, _renderer, _community_renderer, service = _cluster_service(tmp_path)
    now = datetime(2026, 7, 28, 23, 49, 59, tzinfo=ZONE)

    result = asyncio.run(service.deliver_once(object(), now))

    assert result == {"status": "outside_window", "sent": 0, "failed": 0}
    assert database.ranking_deliveries(now.date(), DELIVERY_TYPE) == []


def test_delivery_keeps_domain_link_when_html_renderer_falls_back(monkeypatch, tmp_path):
    _database, domains, _stats, renderer, _community_renderer, service = _cluster_service(
        tmp_path, fail_html=True
    )
    sent = []

    async def send(bot, action, **params):
        sent.append(params["message"])

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(
        delivery_module,
        "public_domain_ranking_url",
        lambda token: f"https://tangtang.secmon.cn/ranking/{token}/",
    )
    monkeypatch.setattr(delivery_module, "call_qq_action", send)

    result = asyncio.run(
        service.deliver_once(object(), datetime(2026, 7, 28, 23, 55, tzinfo=ZONE))
    )

    domain = domains.domain_for_group(1001)
    assert domain is not None
    assert result["sent"] == 2
    assert str(sent[0]) == (
        "image:ranking.png\n"
        f"在线：https://tangtang.secmon.cn/ranking/{domain.public_token}/"
    )
    assert len(renderer.calls) == 1
