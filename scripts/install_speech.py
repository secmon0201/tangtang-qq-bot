"""Prepare the pinned Windows CPU speech runtime without touching bot packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/tts"
SOURCE = ROOT / "downloads/GPT-SoVITS-cpu"


def checked_file(path: Path, digest: str) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError(f"Existing file differs from lock: {path.name}")
    return True


def download(url: str, path: Path, digest: str, verify: bool) -> None:
    if checked_file(path, digest):
        return
    if verify:
        raise FileNotFoundError(path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    if temporary.exists():
        raise ValueError(f"Incomplete download retained for inspection: {temporary.name}")
    with urlopen(Request(url, headers={"User-Agent":"Local-speech-installer"}), timeout=90) as response, temporary.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    checked_file(temporary, digest)
    temporary.rename(path)


def extract(archive: Path, destination: Path, *, strip_root: bool, verify: bool) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            parts = Path(member.filename).parts
            relative = Path(*parts[1:]) if strip_root else Path(*parts)
            if member.is_dir() or not relative.parts:
                continue
            target = (destination / relative).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Archive path escapes speech directory")
            content = package.read(member)
            if target.exists():
                if target.read_bytes() != content:
                    raise ValueError(f"Upstream file changed: {relative}")
            elif verify:
                raise FileNotFoundError(str(relative))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Check existing files only; no installation or startup")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Only Windows-local deployment is supported")
    if not shutil.which("ffmpeg"):
        parser.error("Install ffmpeg and add it to PATH before preparing speech")
    lock = json.loads((ROOT / "config/speech-upstream-lock.json").read_text(encoding="utf-8"))
    revision = lock["runtime"]["revision"]
    archive = ROOT / "downloads" / f"GPT-SoVITS-{revision}.zip"
    download(f"https://api.github.com/repos/RVC-Boss/GPT-SoVITS/zipball/{revision}", archive, lock["runtime"]["archive_sha256"], args.verify)
    extract(archive, SOURCE, strip_root=True, verify=args.verify)
    uv = DATA / "bootstrap/bin/uv.exe"
    python = DATA / "env/Scripts/python.exe"
    if not args.verify:
        if not uv.is_file():
            subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(DATA / "bootstrap"), "uv==0.8.22"], check=True)
        environment = {**os.environ, "UV_PYTHON_INSTALL_DIR":str(DATA / "python")}
        subprocess.run([str(uv), "python", "install", lock["runtime"]["python"]], env=environment, check=True)
        if not python.is_file():
            subprocess.run([str(uv), "venv", "--python", lock["runtime"]["python"], str(DATA / "env")], env=environment, check=True)
        subprocess.run([str(uv), "pip", "install", "--python", str(python), "--extra-index-url", "https://download.pytorch.org/whl/cpu", "--index-strategy", "unsafe-best-match", "-r", str(ROOT / "config/speech-requirements.lock.txt")], check=True)
    elif not python.is_file():
        raise FileNotFoundError("Independent speech Python is absent")
    for filename, digest in lock["voice"]["files"].items():
        download(lock["voice"]["repository"] + "/resolve/master/" + quote(filename), DATA / "dania" / filename, digest, args.verify)
    for filename, digest in lock["prerequisites"]["files"].items():
        cached = DATA / "assets" / filename
        download(lock["prerequisites"]["repository"] + "/resolve/master/" + quote(filename), cached, digest, args.verify)
        if filename.endswith(".zip"):
            destination = SOURCE / "GPT_SoVITS/text" if filename == "G2PWModel.zip" else DATA / "env"
            extract(cached, destination, strip_root=False, verify=args.verify)
        else:
            target = SOURCE / "GPT_SoVITS" / filename
            if not checked_file(target, digest):
                if args.verify:
                    raise FileNotFoundError(filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(cached, target)
    print("Pinned Windows CPU speech files verified. No service started; configure_speech.py binds the voice separately.")


if __name__ == "__main__":
    main()
