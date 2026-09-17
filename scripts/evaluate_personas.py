"""Evaluate 40 synthetic scenarios per persona using the configured chat model."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import httpx
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.persona_profiles import load_personas
from bot.services.speech_policy import delivery_instruction, choose_delivery
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_reply import parse_reply_plan
from bot.services.tangtang_runtime import config_loader
from bot.services.runtime import passive_settings


async def main() -> int:
    config = config_loader.load()
    if not config.enabled or not passive_settings().is_chat_globally_enabled("mention_chat"):
        print("Evaluation stopped: chat model or global cost gate is disabled.")
        return 2
    # The transport envelope contains several answers plus control fields.
    # User-facing message limits are enforced separately by parse_reply_plan.
    config = replace(config, timeout_seconds=60, max_response_chars=16000, max_output_tokens=4000)
    provider = TangtangProvider()
    scenarios = json.loads((ROOT / "tests/fixtures/persona_scenarios.json").read_text(encoding="utf-8"))
    profiles = load_personas()
    fingerprint = hashlib.sha256(json.dumps({
        "scenarios": scenarios, "versions": {k: p.version for k, p in profiles.items()},
        "model": config.model, "api_style": config.api_style, "protocol": 2,
    }, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    output = ROOT / "reports/persona-evaluation" / fingerprint
    output.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    for key, profile in profiles.items():
        identity = "\n\n".join(path.read_text(encoding="utf-8") for name in ("self.md", "soul.md", "identity.md", "persona.md", "scene-expression.md") if (path := profile.resource_dir / name).exists())
        contract = delivery_instruction("可用" if key == "denia" else "未绑定声线", False, ("smile", "laugh", "think", "peek"))
        for offset in range(0, len(scenarios), 5):
            batch = [{**item, "text": item["text"].replace("{call}", profile.call_keyword)} for item in scenarios[offset:offset + 5]]
            target = output / f"{key}-{offset // 5 + 1:02d}.json"
            if target.exists():
                record = json.loads(target.read_text(encoding="utf-8"))
                try:
                    json.loads(record["raw"].strip().removeprefix("```json").removesuffix("```").strip())["answers"]
                except (ValueError, KeyError, TypeError):
                    # Preserve the failed sample and its usage as evidence.
                    target = target.with_stem(target.stem + "-retry1")
            if target.exists():
                record = json.loads(target.read_text(encoding="utf-8"))
            else:
                prompt = ("这是离线场景评测，每条都是独立场景，不继承上一条的上下文；各条的context只属于那一条，没有context时按首次交流。对每条按人格给出实际回复。"
                    "不暴露思考。没有可用工具或实时资讯，没有私人记忆。保持群友边界。\n" + contract
                    + '\n批量包装：仅输出 {"answers":[{"id":"场景ID","reply":上面契约的JSON对象}]}，必须覆盖全部ID。\n'
                    + json.dumps(batch, ensure_ascii=False))
                for attempt in range(2):
                    try:
                        text, usage = await provider.generate(config, identity, prompt)
                        break
                    except httpx.HTTPStatusError as exc:
                        if attempt or exc.response.status_code not in {502, 503, 504}:
                            print(f"Evaluation blocked: HTTP {exc.response.status_code}; configured model unchanged.", flush=True)
                            (output / "blocked.json").write_text(json.dumps({"status":exc.response.status_code, "model":config.model, "persona":key, "batch":offset}, ensure_ascii=False), encoding="utf-8")
                            return 2
                        print(f"Transient HTTP {exc.response.status_code}; retrying once.", flush=True)
                        await asyncio.sleep(3)
                record = {"persona": key, "version": profile.version, "model": config.model, "usage": usage, "raw": text}
                target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            raw = record["raw"].strip()
            if raw.startswith("```json") and raw.endswith("```"):
                raw = raw[7:-3].strip()
            try:
                answers = json.loads(raw)["answers"]
            except (ValueError, KeyError, TypeError):
                failures.append(f"{key} batch {offset}: malformed output")
                continue
            mapped = {a["id"]: a["reply"] for a in answers if isinstance(a, dict) and "id" in a and "reply" in a}
            for item in batch:
                payload = mapped.get(item["id"], {})
                plan = parse_reply_plan(json.dumps(payload, ensure_ascii=False))
                decision = choose_delivery(plan, item["text"], available=key == "denia", random_candidate=False)
                valid = plan.structured and plan.decided and bool(plan.messages)
                if not valid:
                    failures.append(f"{key}/{item['id']}: invalid reply")
                issues = []
                if any(marker in plan.text for marker in ("<think>", "L1思维：", "内心独白：", "情绪数值：")):
                    issues.append("exposed_internal_state")
                if item.get("text_only") and decision.voice:
                    issues.append("ignored_text_request")
                if issues:
                    failures.append(f"{key}/{item['id']}: {','.join(issues)}")
                results.append({"persona":key, "id":item["id"], "category":item["category"], "question":item["text"], "context":item.get("context", ""), "review":item.get("review", "辨识度、自然度、事实准确性、工具可靠性"), "reply":plan.text, "voice":plan.voice, "would_send_voice":decision.voice, "format_valid":valid, "issues":issues})
            print(f"Evaluated {key}: {offset + len(batch)}/{len(scenarios)}", flush=True)
    report = {"fingerprint":fingerprint, "scenario_count":len(scenarios), "reply_count":len(results), "failures":failures, "manual_review_required":True, "results":results}
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "comparison.md").write_text("# 人格离线对比\n\n自动检查只证明格式与明确行为约束；下列场景仍需人工评估辨识度、自然度、事实准确性和工具可靠性。\n\n" + "\n\n".join(f"### {r['persona']} / {r['id']}\n\n{r['question']}\n\n上下文：{r['context'] or '无'}\n\n{r['reply']}\n\n评估要点：{r['review']}\n\n语音决策：{r['voice']}；最终语音：{r['would_send_voice']}" for r in results), encoding="utf-8")
    print(f"Completed {len(results)} replies; format failures: {len(failures)}")
    print(f"Review report: {output / 'comparison.md'}")
    return 0 if len(results) == len(scenarios) * 2 and not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
