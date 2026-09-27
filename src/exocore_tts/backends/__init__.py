"""Backend registry: which engines this daemon can actually serve."""
from __future__ import annotations

from exocore_tts.backends.base import AudioResult, Backend
from exocore_tts.backends.fake import FakeBackend
from exocore_tts.backends.voxcpm2 import VoxCpm2Backend

__all__ = ["AudioResult", "Backend", "FakeBackend", "VoxCpm2Backend", "default_backends"]


def default_backends() -> dict[str, Backend]:
    """Engines a production daemon serves, keyed by the assets' ``engine`` field.

    Construction is deliberately import-light: ``VoxCpm2Backend`` does not touch torch or
    voxcpm until a request actually needs the model, so the daemon can start and answer
    ``/health`` as ``cold`` without paying for the heavy stack.
    """
    return {"voxcpm2": VoxCpm2Backend()}
