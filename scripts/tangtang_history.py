from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="查看某位用户与糖糖的互动记录")
    parser.add_argument("user_id", type=int, help="QQ 号")
    parser.add_argument("--group", type=int, default=None, help="只查指定群")
    parser.add_argument("--limit", type=int, default=50, help="最多显示条数")
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parents[1] / "data" / "tangtang" / "tangtang.db"),
        help="tangtang.db 路径",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.group is None:
            rows = conn.execute(
                "SELECT * FROM tangtang_calls WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (args.user_id, args.limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tangtang_calls WHERE user_id = ? AND group_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (args.user_id, args.group, args.limit),
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("没有找到记录。")
        return
    for row in reversed(rows):
        if row["reply_kind"] == "model":
            kind = "模型"
        elif row["reply_kind"] == "feature":
            kind = "本地功能"
        elif row["reply_kind"] == "proactive":
            kind = "主动"
        else:
            kind = "台词"
        print(f"[{row['created_at']}] 群 {row['group_id']} 模式 {row['mode']}（{kind}）")
        print(f"  用户说：{row['call_text']}")
        print(f"  糖糖回：{row['reply_text']}")
    print(f"共 {len(rows)} 条")


if __name__ == "__main__":
    main()
