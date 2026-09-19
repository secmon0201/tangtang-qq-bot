"""Run the golden quality set offline or through the deterministic router."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.services.quality_eval import (
    evaluate_results,
    load_golden_set,
    quality_summary,
)
from bot.services.skills import registry_loader
from bot.services.tangtang_features import classify_local_feature
from bot.application.local_features import request_from_decision


ROOT = Path(__file__).resolve().parent.parent


def _live_classification(cases) -> tuple[dict[str, dict], list[dict]]:
    registry = registry_loader.load()
    action_to_skill = registry.action_map
    results: dict[str, dict] = {}
    metrics: list[dict] = []
    for case in cases:
        decision = classify_local_feature(case.input)
        row: dict = {"case_id": case.case_id}
        if decision is None:
            row.update(skill_id="", action="", args="")
        else:
            request = request_from_decision(decision)
            skill = action_to_skill.get(request.action)
            row.update(
                skill_id=skill.skill_id if skill else "",
                action=request.action,
                args=request.args,
                reply=decision.line,
            )
        results[case.case_id] = row
        metrics.append({"case_id": case.case_id, "ok": True, "status": "ok"})
    return results, metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=ROOT / "config" / "quality-golden-set.json")
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--classify-live", action="store_true")
    parser.add_argument("--print", action="store_true")
    args = parser.parse_args()

    cases = load_golden_set(args.golden)
    if args.classify_live:
        cases = tuple(case for case in cases if case.router_expected)
    metrics: list[dict] = []
    if args.classify_live:
        results, metrics = _live_classification(cases)
    elif args.results:
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        results = dict(payload.get("results") or payload)
        metrics = list(payload.get("metrics") or [])
    else:
        raise SystemExit("provide --results or --classify-live")

    report = evaluate_results(cases, results)
    report["slo"] = quality_summary(metrics)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    if args.print or not args.output:
        print(text)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
