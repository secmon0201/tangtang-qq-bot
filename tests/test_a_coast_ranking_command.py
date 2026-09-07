from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot
import pytest
from fastapi import HTTPException
from nonebot.adapters.onebot.v11 import Message

nonebot.init()

import bot.plugins.commands as commands
from bot.application.local_features import registered_local_features


CLUSTER_GROUP_IDS = (910000101, 910000102, 910000103, 910000104, 910000105)


def test_retired_community_route_returns_404_after_matchers_are_registered():
    with pytest.raises(HTTPException) as captured:
        asyncio.run(commands.retired_community_route())

    assert captured.value.status_code == 404


def test_personal_message_ranking_is_not_exposed_or_registered():
    help_text = commands.user_help_text()
    for command in (
        "#个人发言统计",
        "#个人发言榜",
        "#个人统计",
        "#我的发言统计",
        "#我的发言榜",
        "#我的统计",
    ):
        assert command not in help_text
    assert not hasattr(commands, "personal_message_stats")
    assert "personal_stats" not in registered_local_features()


class _Finished(Exception):
    pass


def test_ranking_cannot_be_queried_without_a_target_group(monkeypatch):
    responses: list[str] = []

    class Matcher:
        async def finish(self, message):
            responses.append(str(message))
            raise _Finished

    monkeypatch.setattr(commands, "current_group", lambda event: None)

    with pytest.raises(_Finished):
        asyncio.run(
            commands.finish_message_ranking(
                Matcher(), SimpleNamespace(user_id=42), Message("日"), cluster=True
            )
        )

    assert responses == ["发言榜需要在目标 QQ 群内查询。"]


def test_cluster_ranking_uses_web_capture_and_appends_its_bearer_link(monkeypatch, tmp_path):
    delivered: list[Message] = []
    token = "A" * 43
    domain = SimpleNamespace(
        domain_id=7,
        mode="cluster",
        name="A海岸",
        alias="A海岸",
        public_token=token,
    )

    class StatsService:
        def ranking_rows_for_groups(self, scope: str, group_ids):
            assert scope == "day"
            assert tuple(group_ids) == CLUSTER_GROUP_IDS
            return [{"rank": 1, "nickname": "测试成员", "message_count": 7, "group_name": "修会"}]

    class Domains:
        def domain_for_group(self, group_id):
            assert group_id == CLUSTER_GROUP_IDS[0]
            return domain

        def domain_groups(self, domain_id):
            assert domain_id == 7
            return CLUSTER_GROUP_IDS

        def public_group_key(self, group_id):
            return "member-key"

    async def build_payload(scope, group_key, **kwargs):
        assert (scope, group_key) == ("day", "domain")
        assert kwargs["domain"] is domain
        assert kwargs["selected_group_id"] is None
        assert len(kwargs["rows"]) == 1
        return {
            "scope": "day",
            "group": "domain",
            "title": "A海岸今日发言榜",
            "subtitle": "历史数据最早自 2026-08-29",
            "chart": {"kind": "group", "rows": [{"label": "修会"}] * 5},
        }

    async def render_web(payload):
        assert payload["scope"] == "day"
        assert payload["group"] == "domain"
        assert payload["chart"]["kind"] == "group"
        assert len(payload["chart"]["rows"]) == 5
        image = tmp_path / "ranking.png"
        image.write_bytes(b"png")
        return image

    class Matcher:
        async def finish(self, message):
            delivered.append(message)

    monkeypatch.setattr(commands, "stats_service", StatsService())
    monkeypatch.setattr(commands, "current_group", lambda event: CLUSTER_GROUP_IDS[0])
    monkeypatch.setattr(commands, "group_domains", lambda: Domains())
    monkeypatch.setattr(commands, "user_report_rows", lambda rows: list(rows))
    monkeypatch.setattr(commands, "build_community_ranking_payload", build_payload)
    monkeypatch.setattr(commands.community_web_renderer, "render_ranking", render_web)

    asyncio.run(
        commands.finish_message_ranking(
            Matcher(), SimpleNamespace(user_id=42), Message("日"), cluster=True
        )
    )

    assert len(delivered) == 1
    assert f"在线：https://bot.example.invalid/ranking/{token}/" in str(delivered[0])
    assert any(segment.type == "image" for segment in delivered[0])


def test_a_coast_command_uses_generic_cluster_name_lookup(monkeypatch):
    domain = SimpleNamespace(domain_id=7, mode="cluster", name="A海岸", alias="")
    calls = []

    class Event:
        user_id = 42

        @staticmethod
        def get_plaintext():
            return "#A海岸发言排行 月"

    class Domains:
        @staticmethod
        def cluster_by_name_or_alias(name):
            assert name == "A海岸"
            return domain

    class Matcher:
        async def finish(self, message):
            raise AssertionError(message)

    async def finish_ranking(matcher, event, args, cluster, **kwargs):
        calls.append((matcher, event, str(args), cluster, kwargs))

    monkeypatch.setattr(commands, "group_domains", lambda: Domains())
    monkeypatch.setattr(commands, "finish_message_ranking", finish_ranking)

    asyncio.run(commands.finish_named_cluster_ranking(Matcher(), Event()))

    assert calls[0][2:] == (
        "月",
        True,
        {"target_domain": domain, "command_name": "A海岸发言排行"},
    )


def test_named_cluster_matcher_ignores_unknown_names(monkeypatch):
    class Event:
        @staticmethod
        def get_plaintext():
            return "#我的统计"

    class Domains:
        @staticmethod
        def cluster_by_name_or_alias(name):
            assert name == "我的"
            return None

    monkeypatch.setattr(commands, "group_domains", lambda: Domains())

    assert not commands.named_cluster_ranking_rule(Event())


def test_named_cluster_ranking_rejects_non_member_group(monkeypatch):
    responses = []
    requested = SimpleNamespace(
        domain_id=7, mode="cluster", name="测试集群", alias="", public_token="A" * 43
    )
    current = SimpleNamespace(
        domain_id=8, mode="cluster", name="其他集群", alias="", public_token="B" * 43
    )

    class Matcher:
        async def finish(self, message):
            responses.append(str(message))
            raise _Finished

    class Domains:
        @staticmethod
        def domain_for_group(group_id):
            assert group_id == 910000101
            return current

        @staticmethod
        def domain_display_name(domain):
            return domain.name

    monkeypatch.setattr(commands, "current_group", lambda event: 910000101)
    monkeypatch.setattr(commands, "group_domains", lambda: Domains())

    with pytest.raises(_Finished):
        asyncio.run(
            commands.finish_message_ranking(
                Matcher(),
                SimpleNamespace(user_id=42),
                Message("日"),
                cluster=True,
                target_domain=requested,
            )
        )

    assert responses == ["当前群不属于“测试集群”集群，不能查看该集群排行。"]
