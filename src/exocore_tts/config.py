"""Path configuration for the voice factory.

Environment overrides (all optional):
    EXOCORE_TTS_HOME            data root                (default: repository root)
    EXOCORE_TTS_VOICE_ROOT      canonical voice assets   (default: <root>/voices)
    EXOCORE_TTS_CANDIDATE_ROOT  casting output root      (default: <root>/candidates)

Paths only for now: the HTTP surface (host / port / bearer) belongs here once the daemon
milestone starts. Nothing in this package may import Django or reach into ExoCore.
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Repository root (this file lives at <repo>/src/exocore_tts/config.py)."""
    return Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    override = os.environ.get("EXOCORE_TTS_HOME", "").strip()
    return Path(override).expanduser().resolve() if override else repo_root()


def _resolve(env_name: str, default: Path) -> Path:
    override = os.environ.get(env_name, "").strip()
    return Path(override).expanduser().resolve() if override else default


def voice_root() -> Path:
    """Directory that owns the canonical voice assets (`voices/<key>/`)."""
    return _resolve("EXOCORE_TTS_VOICE_ROOT", _data_root() / "voices")


def candidate_root() -> Path:
    """Default parent directory for casting batches (`candidates/<batch>/`)."""
    return _resolve("EXOCORE_TTS_CANDIDATE_ROOT", _data_root() / "candidates")
