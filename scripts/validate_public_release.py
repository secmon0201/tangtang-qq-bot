"""Reject instance identities and credentials from a public source snapshot."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path, PurePosixPath

try:
    from scripts.validate_repository import git_candidates
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
    from validate_repository import git_candidates


PRIVATE_TEMPLATE_KEYS = frozenset(
    {
        "QQ_ACCOUNT_ID",
        "MANAGED_GROUP_IDS",
        "A_COAST_GROUP_IDS",
        "BOT_OPERATOR_IDS",
        "GLOBAL_ANNOUNCEMENT_OPERATOR_IDS",
        "GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER",
        "ONEBOT_ACCESS_TOKEN",
        "CODEX_COMPLETION_NOTIFY_GROUP_ID",
        "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID",
        "CODEX_COMPLETION_NOTIFY_TOKEN",
        "DUPLICATE_GROUP_IDS",
        "GAME_GROUP_IDS",
        "GAME_API_GROUP_IDS",
        "BOT_RANDOM_REACTION_GROUP_IDS",
        "HOURLY_ANNOUNCEMENT_GROUP_IDS",
        "ASOUL_BILI_GROUP_IDS",
        "ASOUL_BILI_A_COAST_GROUP_IDS",
        "WUWA_IMPORT_CLUSTER_NAME",
        "QQ_OPENAPI_APP_ID",
        "QQ_OPENAPI_TOKEN",
        "QQ_OPENAPI_APP_SECRET",
        "gsuid_core_ws_token",
        "PROFILE_API_KEY",
        "TANGTANG_GROUP_IDS",
        "TANGTANG_CALL_IGNORE_PROBABILITY",
        "TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS",
        "TANGTANG_API_KEY",
        "PUBLIC_SITE_BASE_URL",
        "PUBLIC_SHORT_HOST",
        "PUBLIC_TUNNEL_NAME",
        "PUBLIC_GENERATOR_CREDIT",
    }
)
LOCAL_PRIVATE_KEYS = PRIVATE_TEMPLATE_KEYS
TEXT_SUFFIXES = frozenset(
    {".bat", ".cfg", ".css", ".html", ".ini", ".js", ".json", ".md", ".ps1", ".py", ".toml", ".txt", ".yaml", ".yml"}
)
PUBLIC_EXTERNAL_IDS = frozenset(
    {
        "672328094",
        "672342685",
        "672353429",
        "703007996",
    }
)
SYNTHETIC_ID_PATTERN = re.compile(r"(?:900|910|920)\d{6}\Z")
EXPLICIT_ID_PATTERN = re.compile(
    r"(?i)(?:qq|account|operator|user|group)(?:_id|id)?[\"']?\s*[:=]\s*[\"']?(\d{9,12})"
)
CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_template(values: dict[str, str]) -> list[str]:
    return [
        f"private template field must be empty: {key}"
        for key in sorted(PRIVATE_TEMPLATE_KEYS)
        if values.get(key, "").strip()
    ]


def _private_literals(values: dict[str, str]) -> dict[str, set[str]]:
    literals: dict[str, set[str]] = {}
    for key in sorted(LOCAL_PRIVATE_KEYS):
        raw_value = values.get(key, "").strip()
        if not raw_value:
            continue
        parts = raw_value.split(",") if (key.endswith("_IDS") or "PROBABILITY" in key) else [raw_value]
        for part in parts:
            value = part.strip()
            if len(value) >= 5 and value not in {"https://api.deepseek.com"}:
                literals.setdefault(value.casefold(), set()).add(key)
    user_profile = os.environ.get("USERPROFILE", "").strip()
    if len(user_profile) >= 5:
        literals.setdefault(user_profile.casefold(), set()).add("USERPROFILE")
    return literals


def _read_text(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2 * 1024 * 1024:
        return None
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return None


def validate_public_files(
    root: Path,
    candidates: list[str],
    *,
    local_values: dict[str, str] | None = None,
) -> list[str]:
    errors: list[str] = []
    private_literals = _private_literals(local_values or {})
    private_path_pattern = re.compile(r"(?i)(?:[a-z]:[\\/]+users[\\/]+[^\\/\s]+|file:[\\/]{2,3}[a-z]:[\\/])")
    local_qq_markers = (("QQ" + "Data"), ("Tencent" + " Files"))
    for raw_path in candidates:
        normalized = PurePosixPath(raw_path.replace("\\", "/")).as_posix()
        path = root / Path(*PurePosixPath(normalized).parts)
        if not path.is_file():
            continue
        text = _read_text(path)
        if text is None:
            continue
        if private_path_pattern.search(text) or any(marker in text for marker in local_qq_markers):
            errors.append(f"machine-specific local path: {normalized}")
        if any(pattern.search(text) for pattern in CREDENTIAL_PATTERNS):
            errors.append(f"credential-like content: {normalized}")
        for candidate_id in EXPLICIT_ID_PATTERN.findall(text):
            if candidate_id in PUBLIC_EXTERNAL_IDS or SYNTHETIC_ID_PATTERN.fullmatch(candidate_id):
                continue
            errors.append(f"non-synthetic QQ-like identifier: {normalized}")
            break
        folded = text.casefold()
        matched_keys = sorted(
            {key for literal, keys in private_literals.items() if literal in folded for key in keys}
        )
        if matched_keys:
            errors.append(
                f"local private value from {','.join(matched_keys)}: {normalized}"
            )
    return errors


def validate_public_release(root: Path, *, include_local_env: bool = True) -> list[str]:
    errors = validate_template(parse_env(root / ".env.example"))
    local_values = parse_env(root / ".env") if include_local_env else {}
    errors.extend(validate_public_files(root, git_candidates(root), local_values=local_values))
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-local-env", action="store_true")
    args = parser.parse_args()
    errors = validate_public_release(args.root.resolve(), include_local_env=not args.no_local_env)
    if errors:
        print("Public release validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Public release validation passed: tracked source is separated from local instance data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
