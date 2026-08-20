"""Render the reusable 今日老婆 introduction card for visual review."""

from pathlib import Path

from bot.services.mini_game_reports import MiniGameReportRenderer


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "reports" / "today_wife_intro"


def main() -> None:
    renderer = MiniGameReportRenderer(OUTPUT_DIR, retention_hours=720)
    print(renderer.render_today_wife_intro_card())


if __name__ == "__main__":
    main()
