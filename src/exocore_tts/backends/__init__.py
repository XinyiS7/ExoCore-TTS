"""Backend registry: which engines this daemon can actually serve."""
from __future__ import annotations

from exocore_tts.backends.base import AudioResult, Backend
from exocore_tts.backends.fake import FakeBackend

__all__ = ["AudioResult", "Backend", "FakeBackend", "default_backends"]


def default_backends() -> dict[str, Backend]:
    """Engines a production daemon serves, keyed by the assets' ``engine`` field.

    ``voxcpm2`` -- the only production engine -- is wired in here once the shared Vox
    low-level entry point exists (Plan/0003 §8 step 4). Until then the daemon still
    answers ``/health`` and every synthesis request fails honestly with
    ``503 engine_unavailable`` instead of pretending to have an engine.
    """
    return {}
