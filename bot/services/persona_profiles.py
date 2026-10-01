"""Versioned character data; character identity never grants platform permissions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from bot.config import ROOT


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    key: str
    name: str
    call_keyword: str
    resource_dir: Path
    version: str

    def prompt(self) -> str:
        return (self.resource_dir / "persona.md").read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    key: str
    endpoint: str
    reference_audio: Path
    reference_text: str
    model_version: str
    reference_hash: str
    gpt_weights: Path | None = None
    sovits_weights: Path | None = None
    language: str = "zh"
    speed: float = 1.0
    runtime_version: str = ""
    prompt_language: str = "zh"
    top_k: int = 15
    top_p: float = 1.0
    temperature: float = 1.0
    text_split_method: str = "cut5"
    fragment_interval: float = 0.3
    seed: int = -1
    repetition_penalty: float = 1.35
    batch_size: int = 1

    @property
    def version(self) -> str:
        payload = (self.key, self.endpoint, self.model_version, self.reference_hash,
                   self.reference_text, self.language, self.speed, self.runtime_version,
                   self.prompt_language, self.top_k, self.top_p, self.temperature,
                   self.text_split_method, self.fragment_interval, self.seed,
                   self.repetition_penalty, self.batch_size, "sovits-api-v2-contract-2")
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChatContext:
    persona: PersonaProfile
    group_id: int
    user_id: int
    request_id: str
    selection_revision: int
    settings_revision: int
    model_profile: str
    proactive: bool = False
    configuration_version: str = ""
    gate_revision: tuple[int, int] = (0, 0)
    private: bool = False


def load_personas(root: Path | None = None) -> dict[str, PersonaProfile]:
    resources = root or ROOT / "bot" / "resources"
    profiles = {}
    for key, name, call, folder in (
        ("tangtang", "糖糖", "糖糖", resources / "tangtang"),
        ("denia", "达妮娅", "娅娅", resources / "personas" / "denia"),
    ):
        digest = hashlib.sha256()
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(folder).as_posix().encode())
                digest.update(path.read_bytes())
        profiles[key] = PersonaProfile(key, name, call, folder, digest.hexdigest())
    return profiles
