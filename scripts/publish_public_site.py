"""Prepare or atomically activate a complete public-site release."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from build_public_site import ROOT, SiteBuildError, build_site


RELEASES_ROOT = ROOT / "data" / "public-site-releases"
POINTER_PATH = ROOT / "data" / "public-site-current.txt"
LOCK_PATH = ROOT / "data" / "public-site-publish.lock"


def _release_id(staging: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        digest.update(path.relative_to(staging).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + digest.hexdigest()[:12]


def _activate(release_id: str) -> None:
    candidate = (RELEASES_ROOT / release_id).resolve()
    try:
        candidate.relative_to(RELEASES_ROOT.resolve())
    except ValueError as exc:
        raise SiteBuildError("release escaped the release root") from exc
    if not (candidate / "_site-manifest.json").is_file():
        raise SiteBuildError(f"release is incomplete: {release_id}")
    POINTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = POINTER_PATH.with_suffix(".tmp")
    temporary.write_text(release_id + "\n", encoding="ascii")
    os.replace(temporary, POINTER_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="activate the validated release with one pointer replacement")
    parser.add_argument("--rollback", metavar="RELEASE_ID", help="activate an existing complete release")
    args = parser.parse_args()
    RELEASES_ROOT.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SiteBuildError("another public-site publish is already running") from exc
    os.close(descriptor)
    try:
        if args.rollback:
            if not args.apply:
                print(f"rollback target validated: {args.rollback}; add --apply to activate it")
                return 0
            _activate(args.rollback)
            print(f"public site atomically rolled back to {args.rollback}")
            return 0
        with tempfile.TemporaryDirectory(prefix="public-site-stage-", dir=RELEASES_ROOT) as temporary:
            staging = Path(temporary)
            build_site(staging)
            release_id = _release_id(staging)
            target = RELEASES_ROOT / release_id
            if target.exists():
                raise SiteBuildError(f"release already exists: {release_id}")
            if not args.apply:
                print(f"release validated without activation: {release_id}")
                return 0
            os.replace(staging, target)
            _activate(release_id)
            print(f"public site atomically activated: {release_id}")
        return 0
    finally:
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
