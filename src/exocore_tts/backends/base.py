"""The minimal contract every synthesis backend implements (Plan/0003 §4.1).

A backend owns one engine (``"voxcpm2"`` today, a cloud vendor later) and translates
factory-owned voice assets into raw audio. It never sees HTTP, never builds a response and
never decides segmentation, verification or model lifetime: those belong to the daemon.

A backend is constructed once and shared by every request of its engine; the daemon calls
``load``/``synthesize``/``unload`` only from the runtime's single worker thread, so a
backend may keep plain instance state about its model without extra locking.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from exocore_tts.voices import VoiceAsset


@dataclass(frozen=True)
class AudioResult:
    """One spoken segment: raw samples plus their rate.

    ``samples`` is a 1-D (mono) or ``(frames, channels)`` array; the service owns the
    structural check and the WAV container, the backend only produces samples.
    """

    samples: Any
    sample_rate: int


class Backend(Protocol):
    """Structural contract; implementations do not need to inherit from anything."""

    engine: str

    def supports_delivery(self) -> bool:
        """True when a non-empty natural-language ``delivery`` can be implemented safely.

        A backend that answers ``False`` must never be handed a non-empty delivery; the
        service refuses the request instead of silently dropping the direction.
        """

    def check_asset(self, asset: VoiceAsset) -> None:
        """Cheap pre-admission check of everything this engine needs from the asset.

        Runs before the model is loaded and before any GPU time is reserved, so a damaged
        asset is refused without paying for a load. Raise `EngineUnavailable` for a broken
        manifest or a missing/unreadable reference clip; returning normally means the
        asset is usable. Backends that need no local files answer with a no-op.
        """

    def load(self) -> Any:
        """Load the heavy model and return the handle the runtime will own.

        Runs on the runtime worker thread. Raising anything means "engine unavailable"
        and the runtime rolls back to ``cold`` so the next request may retry.
        """

    def unload(self, model: Any) -> None:
        """Drop ``model`` and release device memory. Runs on the runtime worker thread."""

    def synthesize(
        self,
        model: Any,
        asset: VoiceAsset,
        text: str,
        delivery: str | None,
    ) -> AudioResult:
        """Speak one already-segmented ``text`` with one frozen ``asset``.

        The reference clip, prompt transcript, baseline recipe, seed and generation
        parameters all live inside the asset or the backend; the daemon supplies only the
        segment and the optional engine-neutral direction.
        """
