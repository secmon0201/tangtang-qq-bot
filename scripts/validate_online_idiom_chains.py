"""Validate offline idioms against fresh public online datasets.

Online datasets are the only source for chain candidates.  The bundled
vocabulary is consulted solely to report missing entries.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys

import httpx

from bot.services.idioms import idiom_words


WORD_RE = re.compile(r"^[\u4e00-\u9fff]{4}$")
ONLINE_SOURCES = (
    "https://raw.githubusercontent.com/ghxter/chinese-xinhua/68037fabbcd23e86b36834c40e63d0ef44df1851/data/idiom.json",
    "https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/fe6d6c2e8baa82187f4c96bbe042e43f96c05666/data/idiom.json",
    "https://raw.githubusercontent.com/mapull/chinese-dictionary/e804ada333b68afddfdccbe8dcc938a72da157a7/idiom/idiom.json",
)


def download_online_words() -> set[str]:
    words: set[str] = set()
    headers = {"Accept": "application/json", "User-Agent": "tangtang-chain-validator/1.0"}
    with httpx.Client(headers=headers, timeout=120, follow_redirects=True) as client:
        for url in ONLINE_SOURCES:
            response = client.get(url)
            response.raise_for_status()
            values = response.json()
            if not isinstance(values, list):
                raise RuntimeError(f"Unexpected online data shape: {url}")
            words.update(
                word
                for value in values
                if isinstance(value, dict)
                for word in (str(value.get("word") or "").strip(),)
                if WORD_RE.fullmatch(word)
            )
    return words


def find_random_chain(words: set[str], length: int, rng: random.Random) -> list[str] | None:
    by_first: dict[str, list[str]] = {}
    for word in words:
        by_first.setdefault(word[0], []).append(word)
    starts = list(words)
    rng.shuffle(starts)

    def extend(path: list[str], used: set[str]) -> bool:
        if len(path) == length:
            return True
        candidates = [word for word in by_first.get(path[-1][-1], []) if word not in used]
        rng.shuffle(candidates)
        for word in candidates:
            path.append(word)
            used.add(word)
            if extend(path, used):
                return True
            used.remove(word)
            path.pop()
        return False

    for start in starts:
        path = [start]
        if extend(path, {start}):
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate offline idioms with online chain candidates.")
    parser.add_argument("--games", type=int, default=100)
    parser.add_argument("--length", type=int, default=20)
    args = parser.parse_args()
    if args.games < 1 or args.length < 2:
        parser.error("--games must be positive and --length must be at least 2")

    online_words = download_online_words()
    offline_words = idiom_words()
    missing = sorted(online_words - offline_words)
    if missing:
        print(f"FAIL online_words={len(online_words)} missing_offline={len(missing)}")
        print("missing_samples=" + "、".join(missing[:20]))
        return 1

    rng = random.Random()
    chains: list[list[str]] = []
    for game in range(args.games):
        chain = find_random_chain(online_words, args.length, rng)
        if chain is None:
            print(f"FAIL game={game + 1}: no online chain with {args.length} words exists")
            return 1
        chains.append(chain)

    print(
        f"PASS online_words={len(online_words)} offline_words={len(offline_words)} "
        f"games={len(chains)} length={args.length}"
    )
    for index, chain in enumerate(chains[:3], start=1):
        print(f"sample_game_{index}=" + " -> ".join(chain))
    return 0


if __name__ == "__main__":
    sys.exit(main())
