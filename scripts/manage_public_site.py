"""Manage public-site pages, sections, and release notes without touching the live site."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from public_site_schema import SiteManifestError, validate_manifest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "site-src"
MANIFEST_PATH = SOURCE_ROOT / "site.json"
RELEASES_PATH = SOURCE_ROOT / "content" / "releases.json"


class SiteManagementError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SiteManagementError(f"cannot read {path}: {exc}") from exc


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def find_page(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    for page in manifest["pages"]:
        if page["key"] == key:
            return page
    raise SiteManagementError(f"unknown page: {key}")


def parse_section(value: str) -> dict[str, str]:
    key, separator, source = value.partition("=")
    if not separator or not key or not source:
        raise SiteManagementError("section must use KEY=site-src-relative-path")
    path = (SOURCE_ROOT / source).resolve()
    try:
        path.relative_to(SOURCE_ROOT.resolve())
    except ValueError as exc:
        raise SiteManagementError(f"section source escaped site-src: {source}") from exc
    if not path.is_file():
        raise SiteManagementError(f"section source does not exist: {source}")
    return {"key": key, "source": Path(source).as_posix()}


def insert_after(items: list[dict[str, Any]], entry: dict[str, Any], after: str | None) -> None:
    if after is None:
        items.append(entry)
        return
    for index, item in enumerate(items):
        if item["key"] == after:
            items.insert(index + 1, entry)
            return
    raise SiteManagementError(f"cannot insert after unknown key: {after}")


def list_structure(manifest: dict[str, Any]) -> None:
    for page in manifest["pages"]:
        sections = ", ".join(section["key"] for section in page["sections"])
        print(f'{page["key"]:12} {page["route"]:18} [{sections}]')


def add_page(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    if any(page["key"] == args.key for page in manifest["pages"]):
        raise SiteManagementError(f"page key already exists: {args.key}")
    if any(page["route"] == args.route for page in manifest["pages"]):
        raise SiteManagementError(f"page route already exists: {args.route}")
    sections = [parse_section(value) for value in args.section]
    page = {
        "key": args.key,
        "route": args.route,
        "output": args.output,
        "title": args.title,
        "description": args.description,
        "canonical": args.canonical or "",
        "theme_color": "",
        "main_id": "main",
        "main_class": "",
        "motion": not args.no_motion,
        "dialog": args.dialog,
        "toast": args.toast,
        "footer": None,
        "sections": sections,
    }
    manifest["pages"].append(page)
    if args.nav_label:
        manifest["navigation"].append({"route": args.route, "label": args.nav_label})


def remove_page(manifest: dict[str, Any], key: str) -> None:
    page = find_page(manifest, key)
    manifest["pages"].remove(page)
    manifest["navigation"] = [item for item in manifest["navigation"] if item["route"] != page["route"]]


def update_page(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    page = find_page(manifest, args.key)
    old_route = page["route"]
    for attribute in ("route", "output", "title", "description", "canonical"):
        value = getattr(args, attribute)
        if value is not None:
            page[attribute] = value
    navigation = next((item for item in manifest["navigation"] if item["route"] == old_route), None)
    if args.remove_from_nav:
        if navigation:
            manifest["navigation"].remove(navigation)
        return
    if navigation and page["route"] != old_route:
        navigation["route"] = page["route"]
    if args.nav_label is not None:
        if navigation:
            navigation["label"] = args.nav_label
        else:
            manifest["navigation"].append({"route": page["route"], "label": args.nav_label})


def add_section(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    page = find_page(manifest, args.page)
    section = parse_section(args.section)
    if any(item["key"] == section["key"] for item in page["sections"]):
        raise SiteManagementError(f'section already exists on {args.page}: {section["key"]}')
    insert_after(page["sections"], section, args.after)


def move_section(manifest: dict[str, Any], args: argparse.Namespace) -> None:
    source_page = find_page(manifest, args.page)
    target_page = find_page(manifest, args.to_page)
    section = next((item for item in source_page["sections"] if item["key"] == args.key), None)
    if section is None:
        raise SiteManagementError(f"unknown section on {args.page}: {args.key}")
    if source_page is target_page and args.after == args.key:
        raise SiteManagementError("a section cannot be placed after itself")
    if source_page is not target_page and any(
        item["key"] == section["key"] for item in target_page["sections"]
    ):
        raise SiteManagementError(f"target already has section key: {args.key}")
    source_page["sections"].remove(section)
    insert_after(target_page["sections"], section, args.after)


def remove_section(manifest: dict[str, Any], page_key: str, section_key: str) -> None:
    page = find_page(manifest, page_key)
    section = next((item for item in page["sections"] if item["key"] == section_key), None)
    if section is None:
        raise SiteManagementError(f"unknown section on {page_key}: {section_key}")
    page["sections"].remove(section)


def add_release(args: argparse.Namespace) -> None:
    releases = load_json(RELEASES_PATH)
    if not isinstance(releases, list):
        raise SiteManagementError("releases.json must contain an array")
    if any(item.get("version") == args.version for item in releases if isinstance(item, dict)):
        raise SiteManagementError(f"release already exists: {args.version}")
    releases.insert(
        0,
        {"version": args.version, "date": args.date, "title": args.title, "items": args.item},
    )
    save_json(RELEASES_PATH, releases)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list pages and their ordered sections")

    add_page_parser = subparsers.add_parser("add-page", help="register a complete page")
    add_page_parser.add_argument("--key", required=True)
    add_page_parser.add_argument("--route", required=True)
    add_page_parser.add_argument("--output", required=True)
    add_page_parser.add_argument("--title", required=True)
    add_page_parser.add_argument("--description", required=True)
    add_page_parser.add_argument("--canonical")
    add_page_parser.add_argument("--nav-label")
    add_page_parser.add_argument("--section", action="append", required=True)
    add_page_parser.add_argument("--dialog", action="store_true")
    add_page_parser.add_argument("--toast", action="store_true")
    add_page_parser.add_argument("--no-motion", action="store_true")

    remove_page_parser = subparsers.add_parser("remove-page", help="unregister a page without deleting source")
    remove_page_parser.add_argument("--key", required=True)

    update_page_parser = subparsers.add_parser("update-page", help="update page metadata or navigation")
    update_page_parser.add_argument("--key", required=True)
    update_page_parser.add_argument("--route")
    update_page_parser.add_argument("--output")
    update_page_parser.add_argument("--title")
    update_page_parser.add_argument("--description")
    update_page_parser.add_argument("--canonical")
    update_page_parser.add_argument("--nav-label")
    update_page_parser.add_argument("--remove-from-nav", action="store_true")

    add_section_parser = subparsers.add_parser("add-section", help="attach an existing section source to a page")
    add_section_parser.add_argument("--page", required=True)
    add_section_parser.add_argument("--section", required=True)
    add_section_parser.add_argument("--after")

    move_parser = subparsers.add_parser("move-section", help="move one section reference between pages")
    move_parser.add_argument("--page", required=True)
    move_parser.add_argument("--key", required=True)
    move_parser.add_argument("--to-page", required=True)
    move_parser.add_argument("--after")

    remove_section_parser = subparsers.add_parser("remove-section", help="detach a section without deleting source")
    remove_section_parser.add_argument("--page", required=True)
    remove_section_parser.add_argument("--key", required=True)

    release_parser = subparsers.add_parser("add-release", help="prepend one published release note")
    release_parser.add_argument("--version", required=True)
    release_parser.add_argument("--date", required=True)
    release_parser.add_argument("--title", required=True)
    release_parser.add_argument("--item", action="append", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "add-release":
        add_release(args)
        print(f"release note added in source only: {args.version}")
        return 0
    manifest = load_json(MANIFEST_PATH)
    if args.command == "list":
        list_structure(manifest)
        return 0
    if args.command == "add-page":
        add_page(manifest, args)
    elif args.command == "update-page":
        update_page(manifest, args)
    elif args.command == "remove-page":
        remove_page(manifest, args.key)
    elif args.command == "add-section":
        add_section(manifest, args)
    elif args.command == "move-section":
        move_section(manifest, args)
    elif args.command == "remove-section":
        remove_section(manifest, args.page, args.key)
    try:
        validate_manifest(manifest, SOURCE_ROOT)
    except SiteManifestError as exc:
        raise SiteManagementError(f"source manifest was not changed: {exc}") from exc
    save_json(MANIFEST_PATH, manifest)
    print(f"source manifest updated: {args.command}; live site unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
