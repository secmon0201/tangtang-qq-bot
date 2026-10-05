"""Measure only deterministic routing, separately from business/model/network latency."""
import json
import statistics
import time
from pathlib import Path

from tangtang_harness.config import DEFAULT_ROOT
from tangtang_harness.router import Router
from tangtang_harness.types import InboundEvent

requests = ["今天发言排行", "#发言排行 周榜", "#今日缘分", "给我一张达妮娅美图", "#枝江直播",
            "今天发言排行然后分析一下", "今天发言排行然后丢给谁都行", "不要查询发言排行",
            "#发言记录", "#成语炸弹", "第二名是谁", "你好，今天心情怎么样"]
router = Router()
samples = []
for iteration in range(1200):
    for text in requests:
        event = InboundEvent(str(iteration), 103, 101, 102, text)
        start = time.perf_counter_ns()
        router.route(event)
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
ordered = sorted(samples)
report = {"samples": len(samples), "median_ms": statistics.median(samples),
          "p95_ms": ordered[int(len(ordered) * .95)], "maximum_ms": max(samples),
          "target_p95_ms": 20, "model_calls": 0, "qq_writes": 0,
          "scope": "deterministic Router.route only; excludes network, rendering and model"}
report["meets_target"] = report["p95_ms"] <= report["target_p95_ms"]
target = DEFAULT_ROOT / "runtime" / "verification" / "route_benchmark.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
