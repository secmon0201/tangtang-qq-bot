from __future__ import annotations

from bot.config import A_COAST_GROUP_IDS
from bot.db import Database
from bot.services.group_domains import FEATURE_SPECS, GroupDomainService


def _service(tmp_path) -> tuple[Database, GroupDomainService]:
    database = Database(tmp_path / "bot.db")
    database.seed_groups(A_COAST_GROUP_IDS)
    service = GroupDomainService(database)
    service.bootstrap()
    return database, service


def test_new_solo_group_uses_passive_command_defaults(tmp_path):
    _database, service = _service(tmp_path)

    service.ensure_group(9001, group_name="外部测试群", joined_at="2026-08-29T10:00:00+08:00")

    configured = {
        str(row["key"]): bool(row["configured_enabled"])
        for row in service.feature_rows(9001, include_internal=True)
    }
    assert configured == {
        "speech_ranking": True,
        "speech_ranking_push": False,
        "nte": True,
        "zhijiang_calendar": True,
        "mini_games": True,
        "today_wife": True,
        "passive_interaction": False,
        "mention_chat": True,
        "proactive_chat": False,
        "hourly": False,
        "bilibili": False,
        "speech_archive": True,
        "live_guard": True,
        "duplicate": True,
    }
    assert service.joined_date(9001) == "2026-08-29"


def test_cluster_members_start_with_every_feature_enabled(tmp_path):
    _database, service = _service(tmp_path)
    service.ensure_group(9001)
    cluster = service.create_cluster("联动测试集群")

    service.add_group_to_cluster(9001, cluster.domain_id)

    assert all(
        service.feature_enabled(9001, spec.key)
        for spec in FEATURE_SPECS
    )


def test_live_guard_effective_state_depends_on_mini_games(tmp_path):
    _database, service = _service(tmp_path)
    service.ensure_group(9001)

    assert service.effective_feature_enabled(9001, "live_guard")
    service.set_feature(9001, "mini_games", False)
    assert service.feature_enabled(9001, "live_guard")
    assert not service.effective_feature_enabled(9001, "live_guard")


def test_a_coast_cluster_keeps_configured_member_order(tmp_path):
    _database, service = _service(tmp_path)
    domain = service.domain_for_group(A_COAST_GROUP_IDS[0])

    assert domain is not None and domain.mode == "cluster"
    assert service.domain_groups(domain.domain_id) == A_COAST_GROUP_IDS


def test_group_alias_is_the_solo_domain_display_name(tmp_path):
    _database, service = _service(tmp_path)
    service.ensure_group(9001, group_name="外部测试群")
    domain = service.domain_for_group(9001)
    assert domain is not None

    service.set_alias(9001, "星河")

    assert service.display_name(9001) == "星河"
    assert service.domain_display_name(domain) == "星河"


def test_cluster_membership_changes_invalidate_every_old_link(tmp_path):
    _database, service = _service(tmp_path)
    service.ensure_group(9001)
    solo = service.domain_for_group(9001)
    cluster = service.create_cluster("联动测试集群")
    assert solo is not None

    service.add_group_to_cluster(9001, cluster.domain_id)
    joined_cluster = service.domain_for_group(9001)

    assert joined_cluster is not None
    assert service.domain_by_token(solo.public_token) is None
    assert service.domain_by_token(cluster.public_token) is None
    assert service.domain_by_token(joined_cluster.public_token) == joined_cluster

    cluster_token = joined_cluster.public_token
    service.remove_group_from_cluster(9001)
    restored_solo = service.domain_for_group(9001)

    assert restored_solo is not None and restored_solo.mode == "solo"
    assert service.domain_by_token(cluster_token) is None
    assert service.domain_by_token(solo.public_token) is None
    assert service.domain_by_token(restored_solo.public_token) == restored_solo


def test_disabled_group_link_stays_invalid_until_real_reactivation(tmp_path):
    database, service = _service(tmp_path)
    service.ensure_group(9001)
    before = service.domain_for_group(9001)
    assert before is not None

    service.disable_group(9001)
    database.seed_groups((9001,))

    assert database.managed_group(9001) is None
    assert service.domain_by_token(before.public_token) is None

    service.ensure_group(9001)
    after = service.domain_for_group(9001)
    assert after is not None
    assert after.public_token != before.public_token
    assert service.domain_by_token(after.public_token) == after
