"""Backend registry: which engines this daemon can actually serve."""
from __future__ import annotations

from exocore_tts.backends.base import AudioResult, Backend
from exocore_tts.backends.fake import FakeBackend
from exocore_tts.backends.gemini import GeminiBackend
from exocore_tts.backends.voxcpm2 import VoxCpm2Backend

__all__ = [
    "AudioResult",
    "Backend",
    "FakeBackend",
    "GeminiBackend",
    "VoxCpm2Backend",
    "default_backends",
]


def default_backends() -> dict[str, Backend]:
    """Engines a production daemon serves, keyed by the assets' ``engine`` field.

    Construction is deliberately import-light: neither ``VoxCpm2Backend`` (torch/voxcpm)
    nor ``GeminiBackend`` (google-genai plus an API key) touches its dependency here, so the
    daemon can start and answer ``/health`` as ``cold`` without paying for either stack.
    """
    return {"voxcpm2": VoxCpm2Backend(), "gemini": GeminiBackend()}
