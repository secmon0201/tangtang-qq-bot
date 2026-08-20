from datetime import datetime
from pathlib import Path
import sqlite3

from bot.config import settings


def main() -> None:
    if not settings.db_path.exists():
        raise SystemExit(f"Database does not exist: {settings.db_path}")
    backup_dir = settings.db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"bot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(settings.db_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    print(target)


if __name__ == "__main__":
    main()
