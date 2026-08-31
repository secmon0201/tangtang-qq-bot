"""Render the live A Coast and one local-group statistics reports for visual review."""

from __future__ import annotations

from pathlib import Path

from bot.config import settings
from bot.services.avatars import AvatarService
from bot.services.reports import ReportRenderer
from bot.services.runtime import database
from bot.services.stats import StatsService


OUTPUT_DIR = Path("reports/stats_review")
LOCAL_GROUP_ID = 278824712
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
    stats = StatsService(db, realtime_enabled=settings.stats_realtime_enabled)
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
        totals = stats.group_totals(scope)
        avatar_paths = group_avatars.cached_paths(
            [{"user_id": int(row["group_id"])} for row in totals]
        )
        report = renderer.render_ranking(
            stats.ranking_rows(scope),
            f"A海岸{label}发言榜",
            "前 100 名 | 按发言数降序、QQ 号升序",
            show_group_labels=True,
            group_totals=totals,
            group_avatar_paths=avatar_paths,
        )
        generated.append(_save_as(report, f"a_coast_{scope}.png"))

    sample_rows = stats.ranking_rows("total")
    if sample_rows:
        sample = sample_rows[0]
        personal_totals = stats.personal_group_totals(int(sample["user_id"]), "total")
        if personal_totals:
            member_rows = [{**sample, "message_count": sum(int(row["message_count"]) for row in personal_totals)}]
            avatar_paths = AvatarService(
                settings.avatar_cache_dir,
                settings.avatar_base_url,
                settings.avatar_timeout,
                settings.avatar_cache_ttl,
            ).cached_paths(member_rows)
            personal_group_avatars = group_avatars.cached_paths(
                [{"user_id": int(row["group_id"])} for row in personal_totals]
            )
            report = renderer.render_personal_message_stats(
                member_rows[0],
                "个人累计发言统计",
                "五群合计｜仅展示有发言的群｜按发言数降序",
                avatar_paths.get(int(sample["user_id"])),
                personal_totals,
                personal_group_avatars,
            )
            generated.append(_save_as(report, "personal_total.png"))

    group_name = group_names.get(LOCAL_GROUP_ID, str(LOCAL_GROUP_ID))
    trend = stats.recent_group_daily_totals(LOCAL_GROUP_ID)
    for scope, label in SCOPES:
        report = renderer.render_ranking(
            stats.ranking_rows(scope, LOCAL_GROUP_ID),
            f"{group_name}{label}发言榜",
            "前 100 名 | 按发言数降序、QQ 号升序",
            daily_totals=trend,
        )
        generated.append(_save_as(report, f"group_{LOCAL_GROUP_ID}_{scope}.png"))

    for path in generated:
        print(path.resolve())


if __name__ == "__main__":
    main()
