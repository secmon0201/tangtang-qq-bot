"""Build the bundled idiom vocabulary from public, licensed source datasets.

This maintenance command is intentionally separate from the bot runtime.  The
bot only reads ``bot/resources/idioms.txt`` and never calls these remote APIs.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "bot" / "resources" / "idioms.txt"
DEFAULT_WORD_OUTPUT = ROOT / "bot" / "resources" / "four_character_words.txt"
DEFAULT_METADATA = ROOT / "bot" / "resources" / "idioms_sources.json"
WORD_RE = re.compile(r"^[\u4e00-\u9fff]{4}$")
DATASET_ACCEPT = "text/plain,application/json"
HGCHA_ACCEPT = "text/html,application/xhtml+xml"
HGCHA_INDEX_BASE = "https://www.hgcha.com/chengyu"
HGCHA_ROBOTS_URL = "https://www.hgcha.com/robots.txt"
HGCHA_LETTERS = ("A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "W", "X", "Y", "Z")
# Verified entries omitted by the imported public datasets. Keep this list
# deliberately small and documented so a later refresh cannot remove them.
CURATED_WORDS = frozenset({"得偿所愿"})
CURATED_FOUR_CHARACTER_WORDS = frozenset(
    {
        "牛气冲天",
        "勇敢牛牛",
        "水母之歌",
        "云烟成雨",
        "双向奔赴",
        "永不塌房",
        "屁用没有",
        "嘉然小姐",
        "如你所愿",
        "一根手指",
    }
)


@dataclass(frozen=True)
class Source:
    name: str
    repository: str
    license: str
    contents_url: str
    parser: str
    revision: str


SOURCES = (
    Source(
        name="ChID idiom list",
        repository="https://github.com/chujiezheng/ChID-Dataset",
        license="Apache-2.0",
        contents_url="https://raw.githubusercontent.com/chujiezheng/ChID-Dataset/0836ebf72cbe7d2da832021b02ff08f627de885d/Codes%20for%20baseline/idiomList.txt",
        parser="json_array",
        revision="0836ebf72cbe7d2da832021b02ff08f627de885d",
    ),
    Source(
        name="THUOCL idiom list",
        repository="https://github.com/thunlp/THUOCL",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/thunlp/THUOCL/a30ce79d895d01ab5132a5c74c29703ff7efb4cc/data/THUOCL_chengyu.txt",
        parser="weighted_lines",
        revision="a30ce79d895d01ab5132a5c74c29703ff7efb4cc",
    ),
    Source(
        name="by-syk Chinese idiom database",
        repository="https://github.com/by-syk/chinese-idiom-db",
        license="Apache-2.0",
        contents_url="https://raw.githubusercontent.com/by-syk/chinese-idiom-db/6689999d9f0baa759d02d17b1e601c240a9a89ac/chinese-idioms-12976.txt",
        parser="csv_second_field",
        revision="6689999d9f0baa759d02d17b1e601c240a9a89ac",
    ),
    Source(
        name="ghxter Chinese Xinhua idiom database",
        repository="https://github.com/ghxter/chinese-xinhua",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/ghxter/chinese-xinhua/68037fabbcd23e86b36834c40e63d0ef44df1851/data/idiom.json",
        parser="json_objects_word",
        revision="68037fabbcd23e86b36834c40e63d0ef44df1851",
    ),
    Source(
        name="pwxcoo Chinese Xinhua idiom database",
        repository="https://github.com/pwxcoo/chinese-xinhua",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/fe6d6c2e8baa82187f4c96bbe042e43f96c05666/data/idiom.json",
        parser="json_objects_word",
        revision="fe6d6c2e8baa82187f4c96bbe042e43f96c05666",
    ),
    Source(
        name="mapull Chinese dictionary idiom database",
        repository="https://github.com/mapull/chinese-dictionary",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/mapull/chinese-dictionary/e804ada333b68afddfdccbe8dcc938a72da157a7/idiom/idiom.json",
        parser="json_objects_word",
        revision="e804ada333b68afddfdccbe8dcc938a72da157a7",
    ),
)

WORD_SOURCES = (
    Source(
        name="ghxter Chinese Xinhua word database",
        repository="https://github.com/ghxter/chinese-xinhua",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/ghxter/chinese-xinhua/68037fabbcd23e86b36834c40e63d0ef44df1851/data/ci.json",
        parser="json_objects_ci",
        revision="68037fabbcd23e86b36834c40e63d0ef44df1851",
    ),
    Source(
        name="pwxcoo Chinese Xinhua word database",
        repository="https://github.com/pwxcoo/chinese-xinhua",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/pwxcoo/chinese-xinhua/fe6d6c2e8baa82187f4c96bbe042e43f96c05666/data/ci.json",
        parser="json_objects_ci",
        revision="fe6d6c2e8baa82187f4c96bbe042e43f96c05666",
    ),
    Source(
        name="mapull Chinese dictionary word database",
        repository="https://github.com/mapull/chinese-dictionary",
        license="MIT",
        contents_url="https://raw.githubusercontent.com/mapull/chinese-dictionary/e804ada333b68afddfdccbe8dcc938a72da157a7/word/word.json",
        parser="json_objects_word",
        revision="e804ada333b68afddfdccbe8dcc938a72da157a7",
    ),
)


def _read_words(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        word
        for line in path.read_text(encoding="utf-8").splitlines()
        if WORD_RE.fullmatch(word := line.strip())
    }


def _download_source(source: Source) -> tuple[str, bytes]:
    headers = {"Accept": DATASET_ACCEPT, "User-Agent": "tangtang-idiom-importer/1.0"}
    error: httpx.HTTPError | None = None
    for attempt in range(3):
        try:
            response = httpx.get(source.contents_url, headers=headers, timeout=120, follow_redirects=True)
            response.raise_for_status()
            return source.revision, response.content
        except httpx.HTTPError as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Unable to download {source.name}: {error}") from error


def _download_html(url: str) -> str:
    headers = {"Accept": HGCHA_ACCEPT, "User-Agent": "tangtang-idiom-importer/1.0"}
    error: httpx.HTTPError | None = None
    for attempt in range(3):
        try:
            response = httpx.get(url, headers=headers, timeout=45, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"Unable to download {url}: {error}") from error


def _parse_words(source: Source, content: bytes) -> set[str]:
    decoded = content.decode("utf-8")
    if source.parser == "json_array":
        values = json.loads(decoded)
        if not isinstance(values, list):
            raise RuntimeError(f"Unexpected data shape for {source.name}")
        candidates = (value.strip() for value in values if isinstance(value, str))
    elif source.parser == "weighted_lines":
        candidates = (
            line.split(maxsplit=1)[0]
            for line in decoded.splitlines()
            if line.split(maxsplit=1)
        )
    elif source.parser == "csv_second_field":
        candidates = (
            row[1].strip()
            for row in csv.reader(decoded.splitlines())
            if len(row) > 1
        )
    elif source.parser == "json_objects_word":
        values = json.loads(decoded)
        if not isinstance(values, list):
            raise RuntimeError(f"Unexpected data shape for {source.name}")
        candidates = (
            value["word"].strip()
            for value in values
            if isinstance(value, dict) and isinstance(value.get("word"), str)
        )
    elif source.parser == "json_objects_ci":
        values = json.loads(decoded)
        if not isinstance(values, list):
            raise RuntimeError(f"Unexpected data shape for {source.name}")
        candidates = (
            value["ci"].strip()
            for value in values
            if isinstance(value, dict) and isinstance(value.get("ci"), str)
        )
    else:
        raise RuntimeError(f"Unknown parser for {source.name}")
    return {word for word in candidates if WORD_RE.fullmatch(word)}


def _parse_local_word_json(path: Path) -> set[str]:
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise RuntimeError(f"Unexpected local word data shape: {path}")
    return {
        word
        for value in values
        if isinstance(value, dict)
        for word in (str(value.get("word") or value.get("ci") or "").strip(),)
        if WORD_RE.fullmatch(word)
    }


def _metadata_entry(source: Source, revision: str, words: set[str], added: int) -> dict[str, object]:
    return {
        "name": source.name,
        "repository": source.repository,
        "license": source.license,
        "contents_api": source.contents_url,
        "revision": revision,
        "valid_four_character_words": len(words),
        "new_words_added": added,
    }


def _hgcha_page_url(letter: str, page: int) -> str:
    suffix = "" if page == 1 else f"_{page}"
    return f"{HGCHA_INDEX_BASE}/{letter}{suffix}.html"


def _hgcha_page_count(letter: str, content: str) -> int:
    pages = [
        int(value)
        for value in re.findall(rf'href="/chengyu/{re.escape(letter)}_(\d+)\.html"', content)
    ]
    return max(pages, default=1)


def _parse_hgcha_index(content: str) -> set[str]:
    lists = re.findall(r'<ul class="btn w5">(.*?)</ul>', content, re.S)
    for index in lists:
        # The final page for some initials uses ``<li class="t2">`` while
        # earlier pages use bare ``<li>``.  Detail-page links are the stable
        # marker for the public idiom index; remove pinyin spans and their
        # contents before validating the visible title.
        entries = re.findall(r'<a href="/chengyu/[0-9a-f]+\.html">(.*?)</a>', index, re.S)
        if entries:
            return {
                word
                for entry in entries
                if WORD_RE.fullmatch(
                    word := html.unescape(re.sub(r"<span\b[^>]*>.*?</span>", "", entry, flags=re.S)).strip()
                )
            }
    raise RuntimeError("Unable to locate the public HGCHA idiom index on a page")


def _download_hgcha_words() -> tuple[set[str], int]:
    """Collect titles from the site's public, robots-permitted static indexes."""
    robots = _download_html(HGCHA_ROBOTS_URL)
    if "Disallow: /chengyu" in robots:
        raise RuntimeError("HGCHA robots.txt does not permit crawling the public idiom index")
    words: set[str] = set()
    page_count = 0
    for letter in HGCHA_LETTERS:
        first_page = _download_html(_hgcha_page_url(letter, 1))
        pages = _hgcha_page_count(letter, first_page)
        print(f"Collecting HGCHA {letter}: {pages} pages", flush=True)
        entries = _parse_hgcha_index(first_page)
        words.update(entries)
        page_count += 1
        for page in range(2, pages + 1):
            time.sleep(0.15)
            entries = _parse_hgcha_index(_download_html(_hgcha_page_url(letter, page)))
            words.update(entries)
            page_count += 1
    return words, page_count


def _previous_source_metadata(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return []
    return [dict(source) for source in sources if isinstance(source, dict)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Update the offline four-character idiom vocabulary.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--word-output", type=Path, default=DEFAULT_WORD_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-hgcha",
        action="store_true",
        help="Collect public, robots-permitted HGCHA static idiom index pages. This takes several minutes.",
    )
    parser.add_argument(
        "--skip-github",
        action="store_true",
        help="Reuse existing GitHub-source provenance without downloading those sources again.",
    )
    parser.add_argument(
        "--include-four-character-words",
        action="store_true",
        help="Build the separate four-character word vocabulary for entertainment idiom chaining.",
    )
    parser.add_argument(
        "--local-word-json",
        type=Path,
        action="append",
        default=[],
        help="Use a local JSON word list (objects with a word or ci field) instead of downloading word sources.",
    )
    args = parser.parse_args()

    output = args.output.resolve()
    word_output = args.word_output.resolve()
    metadata = args.metadata.resolve()
    words = _read_words(output)
    before_count = len(words)
    curated_added = len(CURATED_WORDS - words)
    words.update(CURATED_WORDS)
    source_metadata: list[dict[str, object]] = []
    previous_sources = _previous_source_metadata(metadata)
    for source in () if args.skip_github else SOURCES:
        revision, content = _download_source(source)
        source_words = _parse_words(source, content)
        added = len(source_words - words)
        words.update(source_words)
        source_metadata.append(_metadata_entry(source, revision, source_words, added))
    if args.include_hgcha:
        hgcha_words, page_count = _download_hgcha_words()
        added = len(hgcha_words - words)
        words.update(hgcha_words)
        source_metadata.append(
            {
                "name": "HGCHA public idiom indexes",
                "repository": "https://www.hgcha.com/chengyu/",
                "robots": HGCHA_ROBOTS_URL,
                "access_method": "public static letter indexes; /search was not accessed",
                "index_pages_collected": page_count,
                "valid_four_character_words": len(hgcha_words),
                "new_words_added": added,
            }
        )
    else:
        source_metadata.extend(
            source
            for source in previous_sources
            if source.get("repository") == "https://www.hgcha.com/chengyu/"
        )
    if args.skip_github:
        source_metadata[0:0] = [
            source
            for source in previous_sources
            if source.get("repository") != "https://www.hgcha.com/chengyu/"
        ]

    word_source_metadata: list[dict[str, object]] = []
    previous_word_sources = []
    try:
        previous_word_sources = json.loads(metadata.read_text(encoding="utf-8")).get("four_character_word_sources", [])
    except (OSError, ValueError, AttributeError):
        pass
    if args.include_four_character_words:
        word_words: set[str] = set()
        word_source_metadata = []
        if args.local_word_json:
            for path in args.local_word_json:
                source_words = _parse_local_word_json(path.resolve())
                added = len(source_words - word_words)
                word_words.update(source_words)
                word_source_metadata.append(
                    {
                        "name": f"Local four-character word data: {path.name}",
                        "repository": "local user-provided JSON",
                        "access_method": "local JSON import",
                        "valid_four_character_words": len(source_words),
                        "new_words_added": added,
                    }
                )
        else:
            for source in WORD_SOURCES:
                revision, content = _download_source(source)
                source_words = _parse_words(source, content)
                added = len(source_words - word_words)
                word_words.update(source_words)
                word_source_metadata.append(_metadata_entry(source, revision, source_words, added))
        word_words.update(CURATED_FOUR_CHARACTER_WORDS)
    else:
        word_words = _read_words(word_output)
        word_source_metadata = previous_word_sources if isinstance(previous_word_sources, list) else []

    if args.dry_run:
        print(f"existing_words={before_count}")
        print(f"merged_words={len(words)}")
        for entry in source_metadata:
            print(f"{entry['name']}: +{entry['new_words_added']}")
        print(f"four_character_words={len(word_words)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    if args.include_four_character_words:
        word_output.write_text("\n".join(sorted(word_words)) + "\n", encoding="utf-8")
    metadata.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "filter": "exactly four CJK Unified Ideographs (U+4E00-U+9FFF)",
                "output": str(output.relative_to(ROOT)).replace("\\", "/"),
                "existing_words_before_import": before_count,
                "total_unique_words": len(words),
                "curated_words": sorted(CURATED_WORDS),
                "curated_words_added": curated_added,
                "sources": source_metadata,
                "four_character_word_sources": word_source_metadata,
                "curated_four_character_words": sorted(CURATED_FOUR_CHARACTER_WORDS),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Updated {output}: {before_count} -> {len(words)} unique words")
    print(f"Wrote provenance to {metadata}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
