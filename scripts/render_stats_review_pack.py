"""Render one live cluster and one member-group statistics report pack."""

from __future__ import annotations

from pathlib import Path

from bot.config import settings
from bot.services.avatars import AvatarService
from bot.services.reports import ReportRenderer
from bot.services.runtime import database, group_domains
from bot.services.stats import StatsService


OUTPUT_DIR = Path("reports/stats_review")
SCOPES = (
    ("day", "今日"),
    ("week", "本周"),
    ("month", "本月"),
    ("total", "总"),
)


def _save_as(path: Path, filename: str) -> Path:
    target = OUTPUT_DIR / filename
    target.unlink(missing_ok=True)
    path.replace(target)
    return target


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db = database()
    domains = group_domains()
    stats = StatsService(
        db,
        realtime_enabled=settings.stats_realtime_enabled,
        group_provider=domains.all_group_ids,
    )
    with db.connect() as connection:
        cluster_row = connection.execute(
            """SELECT domain_id FROM group_domains
               WHERE mode='cluster' AND enabled=1 ORDER BY domain_id LIMIT 1"""
        ).fetchone()
    if cluster_row is None:
        raise RuntimeError("No active cluster is available for the stats review pack")
    cluster_id = int(cluster_row["domain_id"])
    cluster_group_ids = domains.domain_groups(cluster_id)
    if not cluster_group_ids:
        raise RuntimeError("The selected review cluster has no active member groups")
    cluster = domains.domain_for_group(cluster_group_ids[0])
    if cluster is None:
        raise RuntimeError("The selected review cluster is unavailable")
    cluster_label = domains.domain_display_name(cluster)
    local_group_id = cluster_group_ids[0]
    renderer = ReportRenderer(
        OUTPUT_DIR,
        settings.report_font_path,
        settings.report_retention_hours,
        settings.timezone,
        settings.command_prefix,
    )
    group_avatars = AvatarService(
        settings.avatar_cache_dir / "groups",
        "https://p.qlogo.cn/gh/{user_id}/{user_id}/100",
        settings.avatar_timeout,
        settings.avatar_cache_ttl,
    )
    group_names = {
        int(row["group_id"]): str(row["group_name"] or row["group_id"])
        for row in db.managed_groups()
    }

    generated: list[Path] = []
    for scope, label in SCOPES:
        totals = stats.group_totals_for_groups(scope, cluster_group_ids)
        avatar_paths = group_avatars.cached_paths(
            [{"user_id": int(row["group_id"])} for row in totals]
        )
        report = renderer.render_ranking(
            stats.ranking_rows_for_groups(scope, cluster_group_ids),
            f"{cluster_label}{label}发言榜",
            "前 100 名 | 按发言数降序、QQ 号升序",
            show_group_labels=True,
            group_totals=totals,
            group_avatar_paths=avatar_paths,
        )
        generated.append(_save_as(report, f"cluster_{scope}.png"))

    group_name = group_names.get(local_group_id, str(local_group_id))
    trend = stats.recent_group_daily_totals(local_group_id)
    for scope, label in SCOPES:
        report = renderer.render_ranking(
            stats.ranking_rows(scope, local_group_id),
            f"{group_name}{label}发言榜",
            "前 100 名 | 按发言数降序、QQ 号升序",
            daily_totals=trend,
        )
        generated.append(_save_as(report, f"group_{local_group_id}_{scope}.png"))

    for path in generated:
        print(path.resolve())


if __name__ == "__main__":
    main()
