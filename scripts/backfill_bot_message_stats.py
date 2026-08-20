"""Import self-sent A Coast group messages from local NapCat logs since 2026-07-28."""

from __future__ import annotations

import json
from datetime import date

from bot.config import ROOT
from bot.services.runtime import database
from bot.services.stats import StatsService


START_DAY = date(2026, 7, 28)


def main() -> None:
    log_dir = ROOT / "NapCat.Shell" / "logs"
    result = StatsService(database()).import_napcat_outbound_logs(log_dir, START_DAY)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
