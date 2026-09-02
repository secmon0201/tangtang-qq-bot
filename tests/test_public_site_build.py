from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import brotli
from bs4 import BeautifulSoup
from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = str(ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_modular_build_produces_complete_pages_and_runtime_assets(tmp_path):
    builder = load_script("build_public_site")
    output = tmp_path / "site"

    public_manifest = builder.build_site(output)

    script_url = public_manifest["static_assets"]["/app.js"]
    style_url = public_manifest["static_assets"]["/styles.css"]
    assert script_url.startswith("/assets/build/app.") and script_url.endswith(".js")
    assert style_url.startswith("/assets/build/styles.") and style_url.endswith(".css")
    assert (output / script_url.lstrip("/")).is_file()
    assert (output / style_url.lstrip("/")).is_file()
    home = BeautifulSoup((output / "index.html").read_text(encoding="utf-8"), "html.parser")
    release = (output / "release" / "index.html").read_text(encoding="utf-8")
    assert home.find("script", src=script_url) is not None
    assert home.find("link", href=style_url) is not None
    assert public_manifest["public_routes"]["/"] == "index.html"
    assert public_manifest["public_routes"]["/technology"] == "technology/index.html"
    assert public_manifest["public_routes"]["/technology/"] == "technology/index.html"
    assert len(home.select(".capability-tile[data-modal]")) == 9
    assert home.select_one("#quickstart") is not None
    assert len(home.select("#quickstart [data-copy-command]")) == 6
    assert "先看本群设置，再看全局通知" in home.get_text(" ", strip=True)
    assert "#系统设置" not in home.get_text(" ", strip=True)
    assert "试运行版本" in release
    assert "暂不提供本地部署" in release
    assert "集成群独立权限设置，开放群自定义设置" in release
    for command in (
        "#今日直播",
        "#发言排行 周",
        "#今日老婆",
        "#游戏列表",
        "#nte薄荷排行",
        "#群设置",
    ):
        assert home.find(attrs={"data-copy-command": command}) is not None
    for relative_path in set(public_manifest["public_routes"].values()):
        assert (output / relative_path).is_file()


def test_build_fingerprints_referenced_assets_and_excludes_unused_files(tmp_path):
    builder = load_script("build_public_site")
    output = tmp_path / "site"

    public_manifest = builder.build_site(output)
    static_assets = public_manifest["static_assets"]

    assert "/assets/nte-rank-header.jpg" not in static_assets
    assert "/assets/nte-character-1052.png" not in static_assets
    assert "/vendor/lucide.min.js" in static_assets
    assert "/vendor/three.module.min.js" not in static_assets
    assert not list(output.rglob("three.module.min.*"))
    assert not list(output.rglob("bella-sticker.*"))
    assert not list(output.rglob("nte-rank-header.*"))
    assert not list(output.rglob("nte-character-1052.*"))
    assert not list(output.rglob("nte-character-1039.*"))

    for original_url, built_url in static_assets.items():
        assert original_url != built_url
        assert (output / built_url.lstrip("/")).is_file()


def test_build_outputs_valid_precompressed_and_lossless_variants(tmp_path):
    builder = load_script("build_public_site")
    output = tmp_path / "site"

    public_manifest = builder.build_site(output)
    for public_url, sizes in public_manifest["compression"].items():
        if "br" not in sizes:
            continue
        identity = output / public_url.lstrip("/")
        assert brotli.decompress(identity.with_name(identity.name + ".br").read_bytes()) == identity.read_bytes()
        assert gzip.decompress(identity.with_name(identity.name + ".gz").read_bytes()) == identity.read_bytes()
        assert sizes["br"] < sizes["identity"]
        assert sizes["gzip"] < sizes["identity"]


def test_removed_section_does_not_keep_its_assets_in_release(tmp_path, monkeypatch):
    builder = load_script("build_public_site")
    source_root = tmp_path / "site-src"
    shutil.copytree(ROOT / "site-src", source_root)
    manifest_path = source_root / "site.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    experience = next(page for page in manifest["pages"] if page["key"] == "experience")
    experience["sections"] = [
        section for section in experience["sections"] if section["key"] != "social"
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(builder, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(builder, "MANIFEST_PATH", manifest_path)

    public_manifest = builder.build_site(tmp_path / "built")

    assert "/assets/today-fate.gif" not in public_manifest["static_assets"]
    assert "/assets/group-games.gif" not in public_manifest["static_assets"]


def test_build_does_not_read_static_inputs_from_current_compatibility_site(tmp_path, monkeypatch):
    builder = load_script("build_public_site")
    monkeypatch.setattr(builder, "LIVE_SITE", tmp_path / "missing-live-site")

    public_manifest = builder.build_site(tmp_path / "built")

    assert public_manifest["static_assets"]["/assets/tangtang-avatar.jpg"].startswith(
        "/assets/tangtang-avatar."
    )


def test_every_page_uses_unique_section_keys_and_existing_independent_sources():
    manifest = json.loads((ROOT / "site-src" / "site.json").read_text(encoding="utf-8"))
    source_paths = []

    for page in manifest["pages"]:
        keys = [section["key"] for section in page["sections"]]
        assert len(keys) == len(set(keys))
        for section in page["sections"]:
            source = ROOT / "site-src" / section["source"]
            assert source.is_file()
            assert source.suffix == ".html"
            source_paths.append(source)

    assert len(source_paths) == len(set(source_paths))


def test_move_section_changes_only_manifest_ownership():
    manager = load_script("manage_public_site")
    manifest = json.loads((ROOT / "site-src" / "site.json").read_text(encoding="utf-8"))
    working = deepcopy(manifest)
    source = manager.find_page(working, "home")
    target = manager.find_page(working, "experience")
    original = next(section.copy() for section in source["sections"] if section["key"] == "portals")

    manager.move_section(
        working,
        argparse.Namespace(page="home", key="portals", to_page="experience", after="social"),
    )

    assert all(section["key"] != "portals" for section in source["sections"])
    moved = next(section for section in target["sections"] if section["key"] == "portals")
    assert moved == original
    assert target["sections"].index(moved) == next(
        index for index, section in enumerate(target["sections"]) if section["key"] == "social"
    ) + 1


def test_move_section_can_reorder_within_one_page():
    manager = load_script("manage_public_site")
    manifest = json.loads((ROOT / "site-src" / "site.json").read_text(encoding="utf-8"))

    manager.move_section(
        manifest,
        argparse.Namespace(page="home", key="portals", to_page="home", after="hero"),
    )

    home = manager.find_page(manifest, "home")
    assert [section["key"] for section in home["sections"]] == [
        "hero",
        "portals",
        "marquee",
        "capabilities",
        "quickstart",
    ]


def test_homepage_modal_content_keeps_private_super_admin_commands_out():
    modal_content = (ROOT / "site-src" / "scripts" / "01-modal-content.js").read_text(
        encoding="utf-8"
    )
    modal_runtime = (ROOT / "site-src" / "scripts" / "03-modal.js").read_text(
        encoding="utf-8"
    )
    clipboard_runtime = (ROOT / "site-src" / "scripts" / "04-clipboard.js").read_text(
        encoding="utf-8"
    )

    assert "机器人级运行条件" in modal_content
    assert "#系统设置" not in modal_content
    assert "#nte薄荷总排行" in modal_content
    assert "modal-examples" in modal_runtime
    assert "modal-availability" in modal_runtime
    assert "[data-copy-command]" in clipboard_runtime


def test_update_page_keeps_navigation_route_and_label_in_sync():
    manager = load_script("manage_public_site")
    manifest = json.loads((ROOT / "site-src" / "site.json").read_text(encoding="utf-8"))

    manager.update_page(
        manifest,
        argparse.Namespace(
            key="experience",
            route="/people/",
            output=None,
            title="群友页面",
            description=None,
            canonical=None,
            nav_label="群友",
            remove_from_nav=False,
        ),
    )

    page = manager.find_page(manifest, "experience")
    assert page["route"] == "/people/"
    assert page["title"] == "群友页面"
    assert {"route": "/people/", "label": "群友"} in manifest["navigation"]
    assert not any(item["route"] == "/experience/" for item in manifest["navigation"])


def test_add_page_is_validated_and_saved_as_one_manifest_change(tmp_path, monkeypatch):
    manager = load_script("manage_public_site")
    source_root = tmp_path / "site-src"
    shutil.copytree(ROOT / "site-src", source_root)
    new_section = source_root / "pages" / "notes" / "01-hero.html"
    new_section.parent.mkdir(parents=True)
    new_section.write_text('<section id="notes"><h1>Notes</h1></section>', encoding="utf-8")
    manifest_path = source_root / "site.json"
    monkeypatch.setattr(manager, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(manager, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_public_site.py",
            "add-page",
            "--key",
            "notes",
            "--route",
            "/notes/",
            "--output",
            "notes/index.html",
            "--title",
            "Notes",
            "--description",
            "Release notes",
            "--nav-label",
            "Notes",
            "--section",
            "hero=pages/notes/01-hero.html",
        ],
    )

    assert manager.main() == 0
    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manager.find_page(saved, "notes")["sections"] == [
        {"key": "hero", "source": "pages/notes/01-hero.html"}
    ]


def test_invalid_crud_change_never_overwrites_source_manifest(tmp_path, monkeypatch):
    manager = load_script("manage_public_site")
    source_root = tmp_path / "site-src"
    shutil.copytree(ROOT / "site-src", source_root)
    manifest_path = source_root / "site.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    home = manager.find_page(manifest, "home")
    home["sections"] = [home["sections"][0]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    monkeypatch.setattr(manager, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(manager, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["manage_public_site.py", "remove-section", "--page", "home", "--key", "hero"],
    )

    with pytest.raises(manager.SiteManagementError, match="was not changed"):
        manager.main()

    assert manifest_path.read_bytes() == before


def test_add_release_prepends_structured_entry_without_publishing(tmp_path, monkeypatch):
    manager = load_script("manage_public_site")
    releases = tmp_path / "releases.json"
    releases.write_text("[]\n", encoding="utf-8")
    monkeypatch.setattr(manager, "RELEASES_PATH", releases)

    manager.add_release(
        argparse.Namespace(
            version="v1.2.3",
            date="2026-08-29",
            title="模块化更新",
            item=["新增页面清单", "支持板块迁移"],
        )
    )

    assert json.loads(releases.read_text(encoding="utf-8")) == [
        {
            "version": "v1.2.3",
            "date": "2026-08-29",
            "title": "模块化更新",
            "items": ["新增页面清单", "支持板块迁移"],
        }
    ]


def test_nonempty_release_data_generates_release_notes_page(tmp_path, monkeypatch):
    builder = load_script("build_public_site")
    source_root = tmp_path / "site-src"
    shutil.copytree(ROOT / "site-src", source_root)
    (source_root / "content" / "releases.json").write_text(
        json.dumps(
            [
                {
                    "version": "v1.2.3",
                    "date": "2026-08-29",
                    "title": "模块化更新",
                    "items": ["新增页面清单", "支持板块迁移"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(builder, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(builder, "MANIFEST_PATH", source_root / "site.json")
    output = tmp_path / "built"

    builder.build_site(output)

    release = (output / "release" / "index.html").read_text(encoding="utf-8")
    assert "糖糖的" in release
    assert "更新日志" in release
    assert "v1.2.3" in release
    assert "支持板块迁移" in release
    assert '<main id="main">' in release
    assert '<main id="main" class="page-shell">' not in release


def test_publish_requires_explicit_apply_and_uses_atomic_pointer_replacement():
    source = (ROOT / "scripts" / "publish_public_site.py").read_text(encoding="utf-8")

    assert 'parser.add_argument("--apply", action="store_true"' in source
    assert "if not args.apply:" in source
    assert "os.replace(temporary, POINTER_PATH)" in source
    assert "build_site(staging)" in source


def test_manifest_rejects_duplicate_section_ownership(monkeypatch):
    builder = load_script("build_public_site")
    manifest = json.loads((ROOT / "site-src" / "site.json").read_text(encoding="utf-8"))
    duplicate = deepcopy(manifest["pages"][0]["sections"][0])
    duplicate["key"] = "duplicate-source"
    manifest["pages"][1]["sections"].append(duplicate)

    with pytest.raises(builder.SiteBuildError, match="attached more than once"):
        builder.validate_manifest(manifest)


def test_output_validation_rejects_missing_local_resource(tmp_path):
    builder = load_script("build_public_site")
    output = tmp_path / "site"
    output.mkdir()
    (output / "index.html").write_text(
        '<!doctype html><html><body><main id="main"><img src="/assets/missing.png"></main></body></html>',
        encoding="utf-8",
    )

    with pytest.raises(builder.SiteBuildError, match="missing local resource"):
        builder.validate_output(output, {"public_routes": {"/": "index.html"}})
