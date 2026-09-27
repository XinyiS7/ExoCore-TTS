"""Deterministic, GPU-free backend used by the contract tests (Plan/0003 §1/§3).

It speaks the same engine-neutral fields as the real backend -- a voice asset, one text
segment and an optional ``delivery`` -- and records every call, so the tests can assert
what actually crossed the seam without loading torch or talking to a network.

The knobs are deliberate and test-facing only:

* ``load_gate`` / ``synth_gate`` block the worker on an event, so concurrency tests can
  widen the windows they care about instead of racing a timer;
* ``fail_load`` / ``fail_on_text`` inject the failure modes the daemon must map;
* ``synth_delay_s`` keeps the worker busy long enough to observe serialization.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from exocore_tts.backends.base import AudioResult
from exocore_tts.errors import DeliveryUnsupported
from exocore_tts.text import segment_text
from exocore_tts.voices import VoiceAsset

GATE_TIMEOUT_S = 5.0
TONE_HZ = 220.0
AMPLITUDE = 0.05


@dataclass(frozen=True)
class FakeModel:
    """The runtime's opaque handle; carries the rate the fake synthesizes at."""

    sample_rate: int


class FakeBackend:
    """A backend whose audio is deterministic and whose timing is controllable."""

    engine = "fake"

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        samples_per_char: int = 16,
        load_gate: threading.Event | None = None,
        load_delay_s: float = 0.0,
        fail_load: bool = False,
        delivery_supported: bool = True,
        fail_on_text: str | None = None,
        synth_delay_s: float = 0.0,
        synth_started: threading.Event | None = None,
        synth_gate: threading.Event | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.samples_per_char = samples_per_char
        self.load_gate = load_gate
        self.load_delay_s = load_delay_s
        self.fail_load = fail_load
        self.fail_on_text = fail_on_text
        self.delivery_supported = delivery_supported
        self.synth_delay_s = synth_delay_s
        self.synth_started = synth_started
        self.synth_gate = synth_gate

        self._lock = threading.Lock()
        self._active_synthesizers = 0
        self.load_count = 0
        self.unload_count = 0
        self.synth_count = 0
        self.max_concurrent_synth = 0
        self.texts: list[str] = []
        self.deliveries: list[str | None] = []
        self.asset_keys: list[str] = []

    # -- Backend protocol ---------------------------------------------------------------

    def supports_delivery(self) -> bool:
        return self.delivery_supported

    def plan_segments(self, text: str) -> list[str]:
        """Local splitter: the fake keeps the production segmentation shape observable."""
        return segment_text(text)

    def check_asset(self, asset: VoiceAsset) -> None:
        """Nothing to check: the fake consumes no files and no device."""

    def load(self) -> FakeModel:
        with self._lock:
            self.load_count += 1
        if self.load_gate is not None and not self.load_gate.wait(GATE_TIMEOUT_S):
            raise RuntimeError("fake load gate was never released")
        if self.load_delay_s:
            time.sleep(self.load_delay_s)
        if self.fail_load:
            raise RuntimeError("fake model load failure")
        return FakeModel(sample_rate=self.sample_rate)

    def unload(self, model: FakeModel) -> None:
        with self._lock:
            self.unload_count += 1

    def synthesize(
        self,
        model: FakeModel,
        asset: VoiceAsset,
        text: str,
        delivery: str | None,
    ) -> AudioResult:
        import numpy as np

        if delivery and not self.delivery_supported:
            raise DeliveryUnsupported("fake backend has no delivery mapping")
        with self._lock:
            self.synth_count += 1
            self._active_synthesizers += 1
            self.max_concurrent_synth = max(self.max_concurrent_synth, self._active_synthesizers)
            self.texts.append(text)
            self.deliveries.append(delivery)
            self.asset_keys.append(asset.key)
        try:
            if self.synth_started is not None:
                self.synth_started.set()
            if self.synth_gate is not None and not self.synth_gate.wait(GATE_TIMEOUT_S):
                raise RuntimeError("fake synth gate was never released")
            if self.synth_delay_s:
                time.sleep(self.synth_delay_s)
            if self.fail_on_text is not None and self.fail_on_text in text:
                raise RuntimeError(f"fake synthesis failure on {text!r}")
            return AudioResult(samples=self._tone(len(text)), sample_rate=self.sample_rate)
        finally:
            with self._lock:
                self._active_synthesizers -= 1

    # -- internals ----------------------------------------------------------------------

    def _tone(self, chars: int):
        import numpy as np

        frames = max(1, chars * self.samples_per_char)
        timeline = np.arange(frames, dtype=np.float64) / self.sample_rate
        return (np.sin(2.0 * np.pi * TONE_HZ * timeline) * AMPLITUDE).astype(np.float32)
