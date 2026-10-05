"""Retired project-side XutheringWavesUID data import entry point."""

from __future__ import annotations

import argparse
import json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="已停用：项目不再导入、重排或写入上游鸣潮数据"
    )
    result.add_argument("--source-db", help="保留旧参数以给出明确停用提示")
    result.add_argument("--source-players")
    result.add_argument("--target-db")
    result.add_argument("--target-players")
    result.add_argument("--apply", action="store_true")
    return result


def main() -> int:
    parser().parse_args()
    print(
        json.dumps(
            {
                "status": "rejected",
                "reason": "项目已停止导入、重排或写入上游鸣潮数据，请使用上游工具和备份流程",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
