"""Preview or apply a cluster-scoped XutheringWavesUID data import."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config import settings  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.services.group_domains import GroupDomainService  # noqa: E402
from bot.services.wuwa_data_import import WuwaDataImporter, WuwaImportError  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="导入既有鸣潮 Bot 数据；默认仅预览，不写入")
    result.add_argument("--source-db", type=Path, required=True, help="既有 Bot 的 GsData.db")
    result.add_argument("--source-players", type=Path, help="既有 Bot 的 XutheringWavesUID/players")
    result.add_argument("--target-db", type=Path, default=settings.gsuid_core_dir / "data" / "GsData.db")
    result.add_argument("--target-players", type=Path, default=settings.gsuid_core_dir / "data" / "XutheringWavesUID" / "players")
    result.add_argument("--apply", action="store_true", help="通过校验和备份后实际写入")
    return result


def main() -> int:
    args = parser().parse_args()
    cluster_name = os.getenv("WUWA_IMPORT_CLUSTER_NAME", "").strip()
    if not cluster_name:
        print(
            json.dumps(
                {"status": "rejected", "reason": "未配置 WUWA_IMPORT_CLUSTER_NAME"},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2
    domains = GroupDomainService(
        Database(settings.db_path), group_order=settings.managed_group_ids
    )
    try:
        domain = domains.cluster_by_name_or_alias(cluster_name)
    except ValueError:
        domain = None
    if domain is None:
        print(
            json.dumps(
                {"status": "rejected", "reason": "指定集群不存在或名称不唯一"},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 2
    source_players = args.source_players or args.source_db.parent / "XutheringWavesUID" / "players"
    importer = WuwaDataImporter(
        args.source_db,
        args.target_db,
        source_players,
        args.target_players,
        allowed_groups=domains.domain_groups(domain.domain_id),
    )
    try:
        plan = importer.plan()
        audit = importer.apply(plan) if args.apply else plan.audit(applied=False)
    except WuwaImportError as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=True, indent=2))
        return 2
    print(json.dumps({"status": "ok", **audit}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
