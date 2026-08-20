"""Summarize A-Coast profile task runs and per-call latencies from bot.out.log."""

from __future__ import annotations

import re
import sys
from pathlib import Path


PAT_START = re.compile(r"A海岸画像任务开始 user_id=(\d+)")
PAT_CHUNK = re.compile(r"A海岸画像分块完成 user_id=(\d+) 未消费=(\d+) 共(\d+)块")
PAT_EV_DONE = re.compile(r"A海岸画像证据提取完成 user_id=(\d+) 第(\d+)/(\d+)块 已耗时(\d+)s")
PAT_MG_DONE = re.compile(
    r"A海岸画像证据合并调用完成 user_id=(\d+) 第(\d+)轮 第(\d+)/(\d+)块 输出(\d+)字 已耗时(\d+)s"
)
PAT_FIN_DONE = re.compile(r"A海岸画像最终生成完成 user_id=(\d+) 输出(\d+)字 已耗时(\d+)s")
PAT_STORE = re.compile(r"A海岸画像落库完成 user_id=(\d+) 消费(\d+)条 总耗时(\d+)s")
PAT_FAIL = re.compile(r"A海岸画像处理失败 user_id=(\d+): (.+)")
PAT_END = re.compile(r"A海岸画像任务结束 user_id=(\d+)")
PAT_REQ = re.compile(r"Tangtang profile request done; chars=(\d+), latency_ms=(\d+)")


def _ts(line: str) -> str:
    match = re.match(r"^\d{2}-\d{2} (\d{2}:\d{2}:\d{2})", line)
    return match.group(1) if match else ""


def main() -> int:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/bot.out.log")
    after = sys.argv[2] if len(sys.argv) > 2 else "00:00:00"
    text = log_path.read_text(encoding="gbk", errors="replace")

    runs: list[dict] = []
    last_run: dict[int, int] = {}
    last_call = None

    for line in text.splitlines():
        timestamp = _ts(line)
        match = PAT_START.search(line)
        if match:
            uid = int(match.group(1))
            run = {
                "uid": uid,
                "start": timestamp,
                "end": "",
                "msgs": 0,
                "blocks": 0,
                "calls": [],
                "status": "running",
                "total_s": None,
            }
            last_run[uid] = len(runs)
            runs.append(run)
            continue
        match = PAT_CHUNK.search(line)
        if match:
            uid = int(match.group(1))
            if uid in last_run:
                runs[last_run[uid]]["msgs"] = int(match.group(2))
                runs[last_run[uid]]["blocks"] = int(match.group(3))
            continue
        match = PAT_REQ.search(line)
        if match:
            last_call = {
                "t": timestamp,
                "chars": int(match.group(1)),
                "latency": round(int(match.group(2)) / 1000, 1),
            }
            continue
        match = PAT_EV_DONE.search(line) or PAT_MG_DONE.search(line) or PAT_FIN_DONE.search(line)
        if match:
            uid = int(match.group(1))
            if "证据提取完成" in line:
                stage = "证据"
            elif "合并调用完成" in line:
                stage = "合并"
            else:
                stage = "最终"
            if last_call is not None and uid in last_run:
                runs[last_run[uid]]["calls"].append(
                    {
                        "stage": stage,
                        "lat": last_call["latency"],
                        "chars": last_call["chars"],
                        "t": last_call["t"],
                    }
                )
                last_call = None
            continue
        match = PAT_STORE.search(line)
        if match:
            uid = int(match.group(1))
            if uid in last_run:
                runs[last_run[uid]]["status"] = "ok"
                runs[last_run[uid]]["consumed"] = int(match.group(2))
                runs[last_run[uid]]["total_s"] = int(match.group(3))
            continue
        match = PAT_FAIL.search(line)
        if match:
            uid = int(match.group(1))
            if uid in last_run:
                runs[last_run[uid]]["status"] = "failed: " + match.group(2).strip()
            continue
        match = PAT_END.search(line)
        if match:
            uid = int(match.group(1))
            if uid in last_run:
                runs[last_run[uid]]["end"] = timestamp
            continue

    printed = 0
    for run in runs:
        if run["start"] < after:
            continue
        printed += 1
        evidence = [c for c in run["calls"] if c["stage"] == "证据"]
        merges = [c for c in run["calls"] if c["stage"] == "合并"]
        finals = [c for c in run["calls"] if c["stage"] == "最终"]
        print(
            f"{run['start']} -> {run['end']}  user={run['uid']}  "
            f"{run['msgs']}条/{run['blocks']}块  总耗时{run['total_s']}s  {run['status']}"
        )
        if evidence:
            values = " ".join(f"{c['lat']}s" for c in evidence)
            print(f"    证据x{len(evidence)}: {values}  (avg {sum(c['lat'] for c in evidence) / len(evidence):.1f}s)")
        if merges:
            values = " ".join(f"{c['lat']}s" for c in merges)
            print(f"    合并x{len(merges)}: {values}  (avg {sum(c['lat'] for c in merges) / len(merges):.1f}s)")
        if finals:
            values = " ".join(f"{c['lat']}s" for c in finals)
            print(f"    最终x{len(finals)}: {values}")
        if not run["calls"]:
            print("    (无调用记录)")

    if printed:
        calls_all = [
            call
            for run in runs
            if run["start"] >= after
            for call in run["calls"]
        ]
        print(
            f"---- 汇总: {printed} 个任务, {len(calls_all)} 次调用, "
            f"平均 {sum(call['lat'] for call in calls_all) / len(calls_all):.1f}s"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
