"""Run the semantic quality judge against recorded golden-set results."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from bot.services.quality_eval import judge_prompt, load_golden_set, parse_judge


ROOT = Path(__file__).resolve().parent.parent


def _endpoint(api_url: str) -> str:
    url = api_url.rstrip("/")
    return url if url.endswith(("/chat/completions", "/responses")) else url + "/chat/completions"


async def judge(
    *,
    api_url: str,
    api_key: str,
    model: str,
    cases,
    results: dict[str, dict],
    timeout: float,
) -> list[dict[str, Any]]:
    endpoint = _endpoint(api_url)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    scores: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        for case in cases:
            actual = results.get(case.case_id) or {}
            reply = str(actual.get("reply") or "")
            if not reply.strip():
                scores.append(
                    {"case_id": case.case_id, "skipped": "empty_reply"}
                )
                continue
            payload = {
                "model": model,
                "max_tokens": 400,
                "messages": [
                    {"role": "user", "content": judge_prompt(case, reply)}
                ],
            }
            row: dict[str, Any] = {"case_id": case.case_id}
            try:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content")
                    or data.get("output_text")
                    or ""
                )
                row["judge"] = parse_judge(str(content))
            except Exception as exc:
                row["error"] = type(exc).__name__
            scores.append(row)
    return scores


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def summarize(scores: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [row["judge"] for row in scores if isinstance(row.get("judge"), dict)]
    dimensions = ("natural", "factual", "no_fabrication", "persona")
    return {
        "cases": len(scores),
        "judged": len(judged),
        "errors": sum(1 for row in scores if row.get("error")),
        "skipped": sum(1 for row in scores if row.get("skipped")),
        "scores": {
            dimension: _mean([float(row[dimension]) for row in judged])
            for dimension in dimensions
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--golden", type=Path, default=ROOT / "config" / "quality-golden-set.json")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    results = dict(payload.get("results") or payload)
    cases = load_golden_set(args.golden)
    if args.dry_run:
        report = {
            "status": "dry_run",
            "cases": len(cases),
            "endpoint": _endpoint(args.api_url or "https://example.invalid/v1"),
        }
    else:
        if not args.api_url or not args.model:
            raise SystemExit("--api-url and --model are required unless --dry-run")
        scores = asyncio.run(
            judge(
                api_url=args.api_url,
                api_key=args.api_key,
                model=args.model,
                cases=cases,
                results=results,
                timeout=args.timeout,
            )
        )
        report = {"status": "ok", "summary": summarize(scores), "cases": scores}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
