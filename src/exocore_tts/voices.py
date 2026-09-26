"""Canonical voice assets owned by the factory.

A voice is a directory:

    voices/<key>/reference.wav    the frozen reference clip (the thing casting produced)
    voices/<key>/voice.json       the manifest: display name, engine, defaults, provenance

The kit stays engine-agnostic on purpose: `engine` names which backend should consume the
asset, and `generation_defaults` carries backend-specific knobs. ExoCore only ever refers to
the opaque `key`; it never learns how a voice is materialised.

Mapping to ExoCore's `VoiceProfile` (Django side, for whoever binds a preset):

    VoiceProfile.name                 <- key
    VoiceProfile.display_name         <- display_name
    VoiceProfile.engine               <- engine
    VoiceProfile.baseline_instruction <- baseline_instruction
    VoiceProfile.generation_defaults  <- generation_defaults
    VoiceProfile.reference_audio_path <- local hint only; never travels on the wire
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from exocore_tts.config import voice_root

KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_]*")
VOICE_MANIFEST = "voice.json"
DEFAULT_REFERENCE_CLIP = "reference.wav"


def validate_key(key: str) -> str:
    """Voice keys are stable identifiers that also become directory names."""
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise ValueError(
            f"Invalid voice key {key!r}: use lowercase letters, digits and underscores, starting with a letter or digit."
        )
    return key


@dataclass
class VoiceAsset:
    """One canonical voice. Serialised verbatim as `voice.json`."""

    key: str
    display_name: str = ""
    engine: str = "voxcpm2"
    baseline_instruction: str = ""
    prompt_text: str = ""
    reference_clip: str = DEFAULT_REFERENCE_CLIP
    generation_defaults: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "VoiceAsset":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})


def voice_dir(key: str) -> Path:
    return voice_root() / validate_key(key)


def reference_path(key: str) -> Path:
    """Absolute path of a voice's reference clip as recorded in its manifest."""
    asset = load_voice(key)
    return voice_dir(key) / (asset.reference_clip or DEFAULT_REFERENCE_CLIP)


def list_voices() -> list[VoiceAsset]:
    """Every loadable voice, sorted by key. Directories without a manifest are ignored."""
    root = voice_root()
    if not root.is_dir():
        return []
    assets = []
    for entry in sorted(root.iterdir()):
        if (entry / VOICE_MANIFEST).is_file():
            assets.append(_read_manifest(entry / VOICE_MANIFEST))
    return assets


def load_voice(key: str) -> VoiceAsset:
    manifest = voice_dir(key) / VOICE_MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(f"Voice {key!r} has no manifest at {manifest}")
    return _read_manifest(manifest)


def save_voice(asset: VoiceAsset, clip_source: Path, *, force: bool = False) -> Path:
    """Freeze a candidate clip into `voices/<key>/`.

    The clip is copied first and the manifest is written last, so a voice directory is only
    ever recognisable once it is complete (an interrupted write is not a loadable voice).
    """
    validate_key(asset.key)
    target_dir = voice_dir(asset.key)
    manifest = target_dir / VOICE_MANIFEST
    if manifest.exists() and not force:
        raise FileExistsError(f"Voice {asset.key!r} already exists at {target_dir}; pass --force to replace it.")

    clip_source = Path(clip_source)
    if not clip_source.is_file():
        raise FileNotFoundError(f"Reference clip not found: {clip_source}")

    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(clip_source, target_dir / (asset.reference_clip or DEFAULT_REFERENCE_CLIP))
    _write_json_atomic(manifest, asset.to_dict())
    return target_dir


def _read_manifest(path: Path) -> VoiceAsset:
    with path.open("r", encoding="utf-8") as handle:
        return VoiceAsset.from_dict(json.load(handle))


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
