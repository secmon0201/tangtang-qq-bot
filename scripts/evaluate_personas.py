"""Evaluate 40 synthetic scenarios per persona using the configured chat model."""
from __future__ import annotations

import asyncio
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
    config = replace(config, timeout_seconds=60)
    provider = TangtangProvider()
    scenarios = json.loads((ROOT / "tests/fixtures/persona_scenarios.json").read_text(encoding="utf-8"))
    output = ROOT / "reports/persona-evaluation"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    failures = []
    for key, profile in load_personas().items():
        identity = "\n\n".join(path.read_text(encoding="utf-8") for name in ("self.md", "soul.md", "identity.md", "persona.md") if (path := profile.resource_dir / name).exists())
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
                prompt = ("这是离线场景评测，每条是独立的首次群聊，不继承上一条的上下文。对每条按人格给出实际回复。"
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
                results.append({"persona":key, "id":item["id"], "category":item["category"], "question":item["text"], "reply":plan.text, "voice":plan.voice, "would_send_voice":decision.voice, "format_valid":valid})
            print(f"Evaluated {key}: {offset + len(batch)}/{len(scenarios)}", flush=True)
    report = {"scenario_count":len(scenarios), "reply_count":len(results), "failures":failures, "results":results}
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "comparison.md").write_text("# 人格离线对比\n\n" + "\n\n".join(f"### {r['persona']} / {r['id']}\n\n{r['question']}\n\n{r['reply']}\n\n语音决策：{r['voice']}；最终语音：{r['would_send_voice']}" for r in results), encoding="utf-8")
    print(f"Completed {len(results)} replies; format failures: {len(failures)}")
    return 0 if len(results) == len(scenarios) * 2 and not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
