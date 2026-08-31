"""Build the public static site from page, section, script, and style modules."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any

import brotli
from public_site_schema import SiteManifestError, validate_manifest as validate_site_manifest


ROOT = Path(__file__).resolve().parents[1]
LIVE_SITE = ROOT / "site"
SOURCE_ROOT = ROOT / "site-src"
MANIFEST_PATH = SOURCE_ROOT / "site.json"
TOKEN_PATTERN = re.compile(r"{{([a-z_]+)}}")
STATIC_REFERENCE_PATTERN = re.compile(r'/(?:assets|vendor)/[A-Za-z0-9._/-]+')
COMPRESSIBLE_SUFFIXES = frozenset({".css", ".html", ".js", ".json", ".svg", ".txt"})
FINGERPRINT_LENGTH = 12
MINIMUM_COMPRESSION_BYTES = 512
MINIMUM_IMAGE_SAVING_RATIO = 0.10

PAGE_BOOTSTRAP = {
    "home": ("/", "index.html", ["hero", "marquee", "capabilities", "portals"]),
    "experience": (
        "/experience/",
        "experience/index.html",
        ["hero", "signal", "conversation", "social", "memory"],
    ),
    "games": ("/games/", "games/index.html", ["hero", "worlds", "boundaries"]),
    "community": (
        "/community/",
        "community/index.html",
        ["hero", "coast", "memory", "participate"],
    ),
    "operator": (
        "/operator/",
        "operator/index.html",
        ["hero", "content", "participation", "governance", "maintenance"],
    ),
    "technology": (
        "/technology/",
        "technology/index.html",
        ["hero", "runtime", "layers", "web", "security", "upstream"],
    ),
    "release": ("/release/", "release/index.html", ["coming-soon"]),
}

SCRIPT_MARKERS = [
    ("foundation", "document.documentElement.classList.add"),
    ("modal-content", "const modalContent ="),
    ("navigation", "const menuButton ="),
    ("modal", "const dialog ="),
    ("clipboard", "const toast ="),
    ("ranking-scope", "const scopeCopy ="),
    ("section-beacons", 'document.querySelectorAll("main > section")'),
    ("reveal", "const reducedMotion ="),
    ("motion", "if (!reducedMotion && window.gsap"),
    ("icons", "\nwindow.lucide?.createIcons();"),
]

STYLE_MARKERS = [
    ("foundation", ":root"),
    ("home", ".hero {"),
    ("shared-pages", ".page-hero {"),
    ("games-community", ".game-rail {"),
    ("operator-technology-release", ".operator-grid"),
    ("overlays-footer-motion", ".modal {"),
    ("responsive", "@media (min-width: 1401px)"),
]


class SiteBuildError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SiteBuildError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SiteBuildError(f"{path} must contain a JSON object")
    return value


def _render_template(path: Path, values: dict[str, str]) -> str:
    source = path.read_text(encoding="utf-8")
    missing = sorted(set(TOKEN_PATTERN.findall(source)) - values.keys())
    if missing:
        raise SiteBuildError(f"{path} has unresolved tokens: {', '.join(missing)}")
    return TOKEN_PATTERN.sub(lambda match: values[match.group(1)], source)


def _safe_output(root: Path, relative_name: str) -> Path:
    relative = Path(relative_name)
    if relative.is_absolute() or ".." in relative.parts:
        raise SiteBuildError(f"unsafe output path: {relative_name}")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise SiteBuildError(f"output escaped build root: {relative_name}") from exc
    return target


def _fingerprinted_name(name: str, payload: bytes, suffix: str | None = None) -> str:
    source = Path(name)
    digest = hashlib.sha256(payload).hexdigest()[:FINGERPRINT_LENGTH]
    final_suffix = suffix if suffix is not None else source.suffix
    return f"{source.stem}.{digest}{final_suffix}"


def _lossless_webp(source: Path) -> bytes | None:
    if source.suffix.lower() != ".png":
        return None
    from io import BytesIO

    from PIL import Image, ImageChops

    with Image.open(source) as image:
        if getattr(image, "n_frames", 1) != 1:
            return None
        expected = image.convert("RGBA")
        buffer = BytesIO()
        expected.save(buffer, format="WEBP", lossless=True, method=6)
    payload = buffer.getvalue()
    if len(payload) >= source.stat().st_size * (1 - MINIMUM_IMAGE_SAVING_RATIO):
        return None
    with Image.open(BytesIO(payload)) as converted:
        actual = converted.convert("RGBA")
    if actual.size != expected.size or ImageChops.difference(expected, actual).getbbox() is not None:
        return None
    return payload


def _write_precompressed(path: Path) -> dict[str, int]:
    if path.suffix.lower() not in COMPRESSIBLE_SUFFIXES:
        return {}
    payload = path.read_bytes()
    if len(payload) < MINIMUM_COMPRESSION_BYTES:
        return {}
    sizes = {"identity": len(payload)}
    variants = {
        "br": brotli.compress(payload, quality=9),
        "gzip": gzip.compress(payload, compresslevel=9, mtime=0),
    }
    for encoding, compressed in variants.items():
        if len(compressed) >= len(payload):
            continue
        suffix = ".br" if encoding == "br" else ".gz"
        path.with_name(path.name + suffix).write_bytes(compressed)
        sizes[encoding] = len(compressed)
    return sizes


def _active_source_files(manifest: dict[str, Any]) -> set[Path]:
    sources = {
        SOURCE_ROOT / "layouts" / "document.html",
        SOURCE_ROOT / "components" / "header.html",
        SOURCE_ROOT / "components" / "mobile-nav.html",
    }
    sources.update(SOURCE_ROOT / relative for relative in manifest["style_modules"])
    sources.update(SOURCE_ROOT / relative for relative in manifest["script_modules"])
    for page in manifest["pages"]:
        sources.update(SOURCE_ROOT / section["source"] for section in page["sections"])
        if page.get("footer"):
            sources.add(SOURCE_ROOT / "components" / "footer.html")
        if page.get("dialog"):
            sources.add(SOURCE_ROOT / "components" / "dialog.html")
        if page.get("toast"):
            sources.add(SOURCE_ROOT / "components" / "toast.html")
    return sources


def _source_static_references(manifest: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for path in _active_source_files(manifest):
        references.update(STATIC_REFERENCE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if any(page.get("motion", True) for page in manifest["pages"]):
        references.update({"/vendor/gsap.min.js", "/vendor/ScrollTrigger.min.js"})
    return references


def _build_static_assets(
    output_root: Path, manifest: dict[str, Any], styles: bytes, scripts: bytes
) -> tuple[dict[str, str], dict[str, dict[str, int]]]:
    url_map: dict[str, str] = {}
    compression: dict[str, dict[str, int]] = {}
    build_root = output_root / "assets" / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    for original_url, payload, source_name in (
        ("/styles.css", styles, "styles.css"),
        ("/app.js", scripts, "app.js"),
    ):
        name = _fingerprinted_name(source_name, payload)
        target = build_root / name
        target.write_bytes(payload)
        public_url = f"/assets/build/{name}"
        url_map[original_url] = public_url
        compression[public_url] = _write_precompressed(target)

    for original_url in sorted(_source_static_references(manifest)):
        static_root = (SOURCE_ROOT / "static").resolve()
        source = (static_root / original_url.lstrip("/")).resolve()
        try:
            source.relative_to(static_root)
        except ValueError as exc:
            raise SiteBuildError(f"static source escaped site root: {original_url}") from exc
        if not source.is_file():
            raise SiteBuildError(f"referenced static source is missing: {original_url}")
        payload = _lossless_webp(source)
        suffix = ".webp" if payload is not None else source.suffix
        if payload is None:
            payload = source.read_bytes()
        name = _fingerprinted_name(source.name, payload, suffix)
        category = source.parent.name
        target = output_root / category / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        public_url = f"/{category}/{name}"
        url_map[original_url] = public_url
        compression[public_url] = _write_precompressed(target)

    license_sources = [
        path for path in (SOURCE_ROOT / "static" / "vendor").glob("LICENSE-*.txt") if path.is_file()
    ]
    for source in license_sources:
        target = output_root / "vendor" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return url_map, compression


def _optimize_document(document: str, url_map: dict[str, str]) -> str:
    from bs4 import BeautifulSoup

    for source_url in sorted(url_map, key=len, reverse=True):
        document = document.replace(source_url, url_map[source_url])
    soup = BeautifulSoup(document, "html.parser")
    for image in soup.find_all("img"):
        image["decoding"] = "async"
        if image.find_parent(class_="site-header") or image.find_parent(class_="hero-stage"):
            image["fetchpriority"] = "high"
        else:
            image["loading"] = "lazy"
    return str(soup)


def _navigation(manifest: dict[str, Any], current_route: str, mobile: bool) -> str:
    links = []
    for entry in manifest["navigation"]:
        current = ' aria-current="page"' if entry["route"] == current_route else ""
        links.append(f'<a href="{html.escape(entry["route"])}"{current}>{html.escape(entry["label"])}</a>')
    if mobile:
        links.append('<a href="/release/">发布页</a>')
    return "".join(links)


def _footer(page: dict[str, Any]) -> str:
    footer = page.get("footer")
    if not footer:
        return ""
    links = "".join(
        f'<a href="{html.escape(item["route"])}">{html.escape(item["label"])}</a>'
        for item in footer["links"]
    )
    return _render_template(
        SOURCE_ROOT / "components" / "footer.html",
        {
            "footer_subtitle": html.escape(footer["subtitle"]),
            "footer_links": links,
            "footer_location": html.escape(footer["location"]),
            "footer_note": html.escape(footer["note"]),
        },
    )


def _release_content(page: dict[str, Any], fragments: list[str]) -> list[str]:
    releases_path = SOURCE_ROOT / "content" / "releases.json"
    releases = json.loads(releases_path.read_text(encoding="utf-8"))
    if not releases:
        return fragments
    if not isinstance(releases, list):
        raise SiteBuildError("site-src/content/releases.json must contain an array")
    cards = []
    for release in releases:
        if not isinstance(release, dict) or not {"version", "date", "title", "items"} <= release.keys():
            raise SiteBuildError("each release needs version, date, title, and items")
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in release["items"])
        cards.append(
            '<article class="operator-item">'
            f'<span class="section-label">{html.escape(str(release["version"]))} · {html.escape(str(release["date"]))}</span>'
            f'<h3>{html.escape(str(release["title"]))}</h3><ul>{items}</ul></article>'
        )
    return [
        '<section class="page-hero"><div class="page-shell reveal"><h1>糖糖的<br><span class="accent">更新日志</span></h1>'
        '<p>每一次公开更新都在这里留下版本、日期和对群友可见的变化。</p></div></section>',
        '<section class="section" id="changelog"><div class="page-shell"><div class="section-heading reveal"><div>'
        '<span class="section-label">Release notes</span><h2>最近更新</h2></div><p>只记录已经完整发布的内容，不展示过程稿。</p>'
        f'</div><div class="operator-grid reveal">{"".join(cards)}</div></div></section>',
    ]


def _has_release_entries() -> bool:
    releases = json.loads((SOURCE_ROOT / "content" / "releases.json").read_text(encoding="utf-8"))
    return isinstance(releases, list) and bool(releases)


def validate_manifest(manifest: dict[str, Any]) -> None:
    try:
        validate_site_manifest(manifest, SOURCE_ROOT)
    except SiteManifestError as exc:
        raise SiteBuildError(str(exc)) from exc


def validate_output(output_root: Path, public_manifest: dict[str, Any]) -> None:
    from bs4 import BeautifulSoup

    routes = public_manifest["public_routes"]
    for route, relative_name in routes.items():
        target = _safe_output(output_root, relative_name)
        if not target.is_file():
            raise SiteBuildError(f"route {route} points to a missing file: {relative_name}")
    for path in output_root.rglob("*.html"):
        source = path.read_text(encoding="utf-8")
        if TOKEN_PATTERN.search(source):
            raise SiteBuildError(f"unresolved template token in {path}")
        soup = BeautifulSoup(source, "html.parser")
        ids = [tag["id"] for tag in soup.select("[id]")]
        if len(ids) != len(set(ids)):
            raise SiteBuildError(f"duplicate HTML id in {path}")
        for tag, attribute in (("a", "href"), ("img", "src"), ("script", "src"), ("link", "href")):
            for element in soup.find_all(tag):
                reference = element.get(attribute)
                if not reference or not reference.startswith("/") or reference.startswith("//"):
                    continue
                public_path = reference.split("?", 1)[0].split("#", 1)[0]
                if public_path in routes:
                    continue
                local_target = _safe_output(output_root, public_path.lstrip("/"))
                if not local_target.is_file():
                    raise SiteBuildError(f"{path} references missing local resource: {reference}")


def build_site(output_root: Path) -> dict[str, Any]:
    manifest = _read_json(MANIFEST_PATH)
    validate_manifest(manifest)
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SOURCE_ROOT / "static" / "_headers", output_root / "_headers")

    styles = "".join(
        (SOURCE_ROOT / relative).read_text(encoding="utf-8") for relative in manifest["style_modules"]
    ).encode("utf-8")
    scripts = "".join(
        (SOURCE_ROOT / relative).read_text(encoding="utf-8") for relative in manifest["script_modules"]
    ).encode("utf-8")
    url_map, compression = _build_static_assets(output_root, manifest, styles, scripts)

    route_map: dict[str, str] = {}
    for page in manifest["pages"]:
        fragments = [
            (SOURCE_ROOT / section["source"]).read_text(encoding="utf-8")
            for section in page["sections"]
        ]
        release_has_entries = page["key"] == "release" and _has_release_entries()
        if page["key"] == "release":
            fragments = _release_content(page, fragments)
        main_content = "\n".join(fragments)
        extra_scripts = ""
        if page.get("motion", True):
            extra_scripts = '<script src="/vendor/gsap.min.js" defer></script><script src="/vendor/ScrollTrigger.min.js" defer></script>'
        canonical = ""
        if page.get("canonical"):
            canonical = f'<link rel="canonical" href="{html.escape(page["canonical"])}">'
        theme = ""
        if page.get("theme_color"):
            theme = f'<meta name="theme-color" content="{html.escape(page["theme_color"])}">'
        main_attrs = f' id="{html.escape(page["main_id"])}"'
        main_class = "" if release_has_entries else page.get("main_class", "")
        if main_class:
            main_attrs += f' class="{html.escape(main_class)}"'
        header = _render_template(
            SOURCE_ROOT / "components" / "header.html",
            {"desktop_navigation": _navigation(manifest, page["route"], mobile=False)},
        )
        mobile_nav = _render_template(
            SOURCE_ROOT / "components" / "mobile-nav.html",
            {"mobile_navigation": _navigation(manifest, page["route"], mobile=True)},
        )
        document = _render_template(
            SOURCE_ROOT / "layouts" / "document.html",
            {
                "title": html.escape(page["title"]),
                "description": html.escape(page["description"]),
                "theme_color": theme,
                "canonical": canonical,
                "style_url": html.escape(url_map["/styles.css"]),
                "script_url": html.escape(url_map["/app.js"]),
                "extra_scripts": extra_scripts,
                "page_key": html.escape(page["key"]),
                "header": header,
                "mobile_nav": mobile_nav,
                "main_attributes": main_attrs,
                "main_content": main_content,
                "dialog": (SOURCE_ROOT / "components" / "dialog.html").read_text(encoding="utf-8") if page.get("dialog") else "",
                "toast": (SOURCE_ROOT / "components" / "toast.html").read_text(encoding="utf-8") if page.get("toast") else "",
                "footer": _footer(page),
            },
        )
        document = _optimize_document(document, url_map)
        target = _safe_output(output_root, page["output"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8", newline="")
        compression[f"/{page['output']}"] = _write_precompressed(target)
        route_map[page["route"]] = page["output"]
        if page["route"] != "/":
            route_map[page["route"].rstrip("/")] = page["output"]
    route_map["/index.html"] = "index.html"
    public_manifest = {
        "version": 2,
        "public_routes": route_map,
        "static_assets": url_map,
        "compression": compression,
    }
    (output_root / "_site-manifest.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    validate_output(output_root, public_manifest)
    return public_manifest


def compare_with_current(output_root: Path) -> list[str]:
    from bs4 import BeautifulSoup

    manifest = _read_json(MANIFEST_PATH)
    differences = []
    for page in manifest["pages"]:
        current = BeautifulSoup((LIVE_SITE / page["output"]).read_text(encoding="utf-8"), "html.parser")
        built = BeautifulSoup((output_root / page["output"]).read_text(encoding="utf-8"), "html.parser")
        current_main = current.find("main")
        built_main = built.find("main")
        if current_main is None or built_main is None:
            differences.append(f'{page["key"]}: missing main element')
            continue
        current_text = " ".join(current_main.stripped_strings)
        built_text = " ".join(built_main.stripped_strings)
        if current_text != built_text:
            differences.append(f'{page["key"]}: main text changed')
        for selector in ("section", "a", "button", "img"):
            if len(current_main.select(selector)) != len(built_main.select(selector)):
                differences.append(f'{page["key"]}: {selector} count changed')
    return differences


def _split_source(text: str, markers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    positions = []
    for name, marker in markers:
        position = text.find(marker)
        if position < 0:
            raise SiteBuildError(f"cannot find module marker: {marker}")
        positions.append((name, position))
    if positions != sorted(positions, key=lambda item: item[1]):
        raise SiteBuildError("module markers are out of order")
    return [
        (name, text[position : positions[index + 1][1] if index + 1 < len(positions) else None])
        for index, (name, position) in enumerate(positions)
    ]


def bootstrap_source() -> None:
    from bs4 import BeautifulSoup

    if MANIFEST_PATH.exists():
        raise SiteBuildError("site-src/site.json already exists; bootstrap is intentionally one-time")
    for partial_directory in ("pages", "scripts", "styles"):
        path = SOURCE_ROOT / partial_directory
        if path.exists():
            shutil.rmtree(path)
    pages = []
    for key, (route, output, section_names) in PAGE_BOOTSTRAP.items():
        source_path = LIVE_SITE / output
        soup = BeautifulSoup(source_path.read_text(encoding="utf-8"), "html.parser")
        main = soup.find("main")
        body = soup.find("body")
        if main is None or body is None:
            raise SiteBuildError(f"{source_path} has no body/main")
        blocks = [child for child in main.children if getattr(child, "name", None)]
        if len(blocks) != len(section_names):
            raise SiteBuildError(f"{source_path} expected {len(section_names)} main blocks, found {len(blocks)}")
        sections = []
        page_dir = SOURCE_ROOT / "pages" / key
        page_dir.mkdir(parents=True, exist_ok=True)
        for index, (name, block) in enumerate(zip(section_names, blocks, strict=True), start=1):
            relative = Path("pages") / key / f"{index:02d}-{name}.html"
            (SOURCE_ROOT / relative).write_text(str(block), encoding="utf-8", newline="")
            sections.append({"key": name, "source": relative.as_posix()})

        title = soup.title.string if soup.title and soup.title.string else key
        description_tag = soup.find("meta", attrs={"name": "description"})
        canonical_tag = soup.find("link", attrs={"rel": "canonical"})
        theme_tag = soup.find("meta", attrs={"name": "theme-color"})
        footer_tag = soup.find("footer")
        footer = None
        if footer_tag:
            subtitle = footer_tag.select_one(".footer-brand small")
            footer_links = [
                {"route": link.get("href", ""), "label": link.get_text(strip=True)}
                for link in footer_tag.select(".footer-links a")
            ]
            meta = footer_tag.select(".footer-meta span")
            footer = {
                "subtitle": subtitle.get_text(strip=True) if subtitle else "",
                "links": footer_links,
                "location": meta[0].get_text(strip=True) if meta else "",
                "note": meta[1].get_text(strip=True) if len(meta) > 1 else "",
            }
        pages.append(
            {
                "key": key,
                "route": route,
                "output": output,
                "title": title,
                "description": description_tag.get("content", "") if description_tag else "",
                "canonical": canonical_tag.get("href", "") if canonical_tag else "",
                "theme_color": theme_tag.get("content", "") if theme_tag else "",
                "main_id": main.get("id", "main"),
                "main_class": " ".join(main.get("class", [])),
                "motion": bool(soup.find("script", src=re.compile("gsap"))),
                "dialog": soup.find("dialog") is not None,
                "toast": soup.select_one("[data-toast]") is not None,
                "footer": footer,
                "sections": sections,
            }
        )

    home = BeautifulSoup((LIVE_SITE / "index.html").read_text(encoding="utf-8"), "html.parser")
    navigation = [
        {"route": link.get("href", ""), "label": link.get_text(strip=True)}
        for link in home.select(".desktop-nav a")
    ]
    script_modules = []
    scripts_dir = SOURCE_ROOT / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for index, (name, content) in enumerate(_split_source((LIVE_SITE / "app.js").read_text(encoding="utf-8"), SCRIPT_MARKERS)):
        relative = Path("scripts") / f"{index:02d}-{name}.js"
        (SOURCE_ROOT / relative).write_text(content, encoding="utf-8", newline="")
        script_modules.append(relative.as_posix())
    style_modules = []
    styles_dir = SOURCE_ROOT / "styles"
    styles_dir.mkdir(parents=True, exist_ok=True)
    for index, (name, content) in enumerate(_split_source((LIVE_SITE / "styles.css").read_text(encoding="utf-8"), STYLE_MARKERS)):
        relative = Path("styles") / f"{index:02d}-{name}.css"
        (SOURCE_ROOT / relative).write_text(content, encoding="utf-8", newline="")
        style_modules.append(relative.as_posix())
    manifest = {
        "version": 1,
        "style_version": "20260829d",
        "script_version": "20260829c",
        "navigation": navigation,
        "style_modules": style_modules,
        "script_modules": script_modules,
        "pages": pages,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="staging output directory; never defaults to live site")
    parser.add_argument("--check-current", action="store_true", help="compare staged page structure with the current public site")
    parser.add_argument("--bootstrap", action="store_true", help="one-time import of the current site into modular source")
    args = parser.parse_args()
    if args.bootstrap:
        bootstrap_source()
        print(f"bootstrapped modular source at {SOURCE_ROOT}")
        return 0
    if args.output is None:
        parser.error("--output is required; live site writes are intentionally not implicit")
    if args.output.resolve() == LIVE_SITE.resolve():
        parser.error("refusing to build directly into the live site; use publish_public_site.py")
    build_site(args.output)
    if args.check_current:
        differences = compare_with_current(args.output)
        if differences:
            raise SiteBuildError("current-site comparison failed: " + "; ".join(differences))
    print(f"public site built and validated at {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
