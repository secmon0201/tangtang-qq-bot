"""Import self-sent A Coast messages from an explicitly supplied archived log directory."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from bot.services.runtime import database
from bot.services.stats import StatsService


START_DAY = date(2026, 7, 28)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill message statistics from archived legacy logs.")
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--start-day", type=date.fromisoformat, default=START_DAY)
    args = parser.parse_args()
    log_dir = args.log_dir.resolve()
    if not log_dir.is_dir():
        raise SystemExit(f"Archived log directory does not exist: {log_dir}")
    result = StatsService(database()).import_napcat_outbound_logs(log_dir, args.start_day)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
