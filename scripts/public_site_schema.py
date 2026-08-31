"""Shared validation for the public-site source manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class SiteManifestError(RuntimeError):
    pass


def validate_manifest(manifest: dict[str, Any], source_root: Path) -> None:
    pages = manifest.get("pages")
    if not isinstance(pages, list) or not pages:
        raise SiteManifestError("site.json needs a non-empty pages array")
    keys: set[str] = set()
    routes: set[str] = set()
    outputs: set[str] = set()
    section_sources: set[str] = set()
    for page in pages:
        key, route, output = page.get("key"), page.get("route"), page.get("output")
        if not all(isinstance(value, str) and value for value in (key, route, output)):
            raise SiteManifestError("every page needs string key, route, and output")
        if key in keys or route in routes or output in outputs:
            raise SiteManifestError(f"duplicate page identity: {key} / {route} / {output}")
        keys.add(key)
        routes.add(route)
        outputs.add(output)
        if not route.startswith("/") or (route != "/" and not route.endswith("/")):
            raise SiteManifestError(f"page route must use canonical slash form: {route}")
        if not output.endswith(".html"):
            raise SiteManifestError(f"page output must be HTML: {output}")
        sections = page.get("sections")
        if not isinstance(sections, list) or not sections:
            raise SiteManifestError(f"page has no sections: {key}")
        section_keys: set[str] = set()
        for section in sections:
            section_key = section.get("key")
            section_source = section.get("source")
            if not isinstance(section_key, str) or not isinstance(section_source, str):
                raise SiteManifestError(f"invalid section on page: {key}")
            if section_key in section_keys:
                raise SiteManifestError(f"duplicate section key on {key}: {section_key}")
            section_keys.add(section_key)
            if section_source in section_sources:
                raise SiteManifestError(f"section source is attached more than once: {section_source}")
            section_sources.add(section_source)
            if not (source_root / section_source).is_file():
                raise SiteManifestError(f"missing section source: {source_root / section_source}")
    for navigation in manifest.get("navigation", []):
        if navigation.get("route") not in routes:
            raise SiteManifestError(f'navigation points to an unknown page: {navigation.get("route")}')
    for module_key in ("style_modules", "script_modules"):
        for relative_name in manifest.get(module_key, []):
            if not (source_root / relative_name).is_file():
                raise SiteManifestError(f"missing {module_key} source: {relative_name}")
