from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from PIL import Image

from bot.services.tangtang_models import VISION_DETAIL_LEVELS, resolve_model_profile


DEFAULT_MODEL = "deepseek-flash"


def _test_image() -> str:
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "#e53935").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _endpoint(api_url: str, api_style: str) -> str:
    url = api_url.rstrip("/")
    suffix = "/responses" if api_style == "responses" else "/chat/completions"
    return url if url.endswith(suffix) else url + suffix


def _payload(
    model: str,
    api_style: str,
    reasoning_effort: str,
    vision_detail: str,
) -> dict[str, Any]:
    image_url = _test_image()
    prompt = "这是一张测试色块。只回答它的主要颜色，不要解释。"
    if api_style == "responses":
        return {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": image_url,
                            "detail": vision_detail,
                        },
                    ],
                }
            ],
            "max_output_tokens": 512,
            "reasoning": {"effort": reasoning_effort},
        }
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": vision_detail},
                    },
                ],
            }
        ],
        "max_completion_tokens": 512,
        "thinking": {
            "type": "disabled" if reasoning_effort == "none" else "enabled"
        },
    }


async def _run(env_path: Path, model_override: str) -> int:
    values = resolve_model_profile(dotenv_values(env_path))
    api_url = str(values.get("TANGTANG_API_URL") or "").strip()
    api_key = str(values.get("TANGTANG_API_KEY") or "").strip()
    api_style = str(values.get("TANGTANG_API_STYLE") or "responses").strip().lower()
    reasoning_effort = str(
        values.get("TANGTANG_REASONING_EFFORT") or "none"
    ).strip().lower()
    vision_detail = "high"
    model = model_override or str(values.get("TANGTANG_MODEL") or DEFAULT_MODEL).strip()
    if not api_url or not api_key:
        print(json.dumps({"ok": False, "error": "missing Tangtang API configuration"}))
        return 2
    if api_style not in {"responses", "chat_completions"}:
        print(json.dumps({"ok": False, "error": "unsupported API style"}))
        return 2
    if vision_detail not in VISION_DETAIL_LEVELS:
        print(json.dumps({"ok": False, "error": "unsupported vision detail"}))
        return 2
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                _endpoint(api_url, api_style),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=_payload(model, api_style, reasoning_effort, vision_detail),
            )
    except httpx.HTTPError as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    result: dict[str, Any] = {
        "ok": response.is_success,
        "status_code": response.status_code,
        "model": model,
        "api_style": api_style,
        "reasoning_effort": reasoning_effort,
        "vision_detail": vision_detail,
    }
    if response.is_success:
        data = response.json()
        result["has_output"] = bool(data.get("output_text") or data.get("output") or data.get("choices"))
        result["usage_present"] = isinstance(data.get("usage"), dict)
    else:
        try:
            error = response.json().get("error", {})
            result["error_type"] = str(error.get("type") or "api_error")
            result["error_code"] = str(error.get("code") or "")
            result["error_message"] = str(error.get("message") or "")[:300]
        except (TypeError, ValueError):
            result["error_type"] = "non_json_api_error"
    print(json.dumps(result, ensure_ascii=False))
    return 0 if response.is_success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Tangtang vision-model compatibility")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    return asyncio.run(_run(args.env, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
