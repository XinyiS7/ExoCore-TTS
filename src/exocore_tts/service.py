"""Request-level orchestration: validate, segment, synthesize, assemble, verify.

This module owns everything between the HTTP contract and the engine backends: which voice
asset answers a key, how long the request may be, how the text is split, how segments are
joined and what "valid audio" objectively means. It never touches HTTP and never renders an
error body -- it raises the stable domain errors and `server.py` maps them.

The only output format this layer knows is the port's raw WAV byte string (Plan/0003 §2.1).
"""
from __future__ import annotations

import io
import logging
import numbers
import time
from typing import Any, Mapping

from exocore_tts.backends.base import AudioResult, Backend
from exocore_tts.errors import (
    DeliveryUnsupported,
    EngineUnavailable,
    InvalidRequest,
    SynthesisFailed,
    TtsError,
    UnknownVoice,
)
from exocore_tts.runtime import ModelRuntime, RuntimeState
from exocore_tts.text import segment_text
from exocore_tts.voices import VoiceAsset, load_voice, validate_key

logger = logging.getLogger("exocore_tts.service")

MAX_DELIVERY_CHARS = 500  # code constant, not an environment knob (Plan/0003 §6)
GAP_MS = 250  # silence between two local segments (Plan/0003 §4.3)
VALID_CHANNELS = (1, 2)
MAX_SAMPLE_RATE = 384000


class TtsService:
    """One daemon's worth of request handling, over one runtime per engine."""

    def __init__(
        self,
        backends: Mapping[str, Backend],
        *,
        max_text_chars: int,
        idle_unload_seconds: float = 0.0,
        idle_poll_seconds: float | None = None,
    ) -> None:
        if max_text_chars < 1:
            raise ValueError("max_text_chars must be >= 1")
        self._backends: dict[str, Backend] = {}
        self._runtimes: dict[str, ModelRuntime] = {}
        for name, backend in backends.items():
            if backend.engine != name:
                raise ValueError(f"backend for key {name!r} reports engine {backend.engine!r}")
            self._backends[name] = backend
            self._runtimes[name] = ModelRuntime(
                backend,
                idle_unload_seconds=idle_unload_seconds,
                idle_poll_seconds=idle_poll_seconds,
            )
        self._max_text_chars = max_text_chars

    # -- lifecycle ----------------------------------------------------------------------

    def health_state(self) -> str:
        """The one public state: worst-case over engines (M2 ships a single engine)."""
        states = [runtime.state() for runtime in self._runtimes.values()]
        if RuntimeState.LOADING in states:
            return RuntimeState.LOADING.value
        if RuntimeState.READY in states:
            return RuntimeState.READY.value
        return RuntimeState.COLD.value

    def close(self) -> None:
        for runtime in self._runtimes.values():
            runtime.close()

    # -- the request path ---------------------------------------------------------------

    def synthesize(self, *, text: str, voice_key: str, delivery: str | None = None) -> bytes:
        """Produce one complete WAV for one request, or raise a stable domain error."""
        clean_text = self._clean_text(text)
        clean_delivery = self._clean_delivery(delivery)
        asset = self._load_asset(voice_key)
        backend = self._backends.get(asset.engine)
        if backend is None:
            raise EngineUnavailable(f"no backend serves engine {asset.engine!r}")
        backend.check_asset(asset)  # cheap, and it happens before any GPU load
        if clean_delivery and not backend.supports_delivery():
            raise DeliveryUnsupported(f"engine {backend.engine!r} cannot implement delivery")

        segments = segment_text(clean_text)
        started = time.perf_counter()
        runtime = self._runtimes[asset.engine]
        wav = runtime.run(
            lambda model: self._render(backend, model, asset, segments, clean_delivery)
        )
        logger.info(
            "render ok voice=%s engine=%s segments=%d wav_bytes=%d wall=%.2fs",
            asset.key,
            backend.engine,
            len(segments),
            len(wav),
            time.perf_counter() - started,
        )
        return wav

    # -- validation ---------------------------------------------------------------------

    def _clean_text(self, text: Any) -> str:
        if not isinstance(text, str):
            raise InvalidRequest("text must be a string")
        clean = text.strip()
        if not clean:
            raise InvalidRequest("text is blank")
        if len(clean) > self._max_text_chars:
            raise InvalidRequest(f"text exceeds {self._max_text_chars} characters")
        return clean

    def _clean_delivery(self, delivery: Any) -> str | None:
        if delivery is None:
            return None
        if not isinstance(delivery, str):
            raise InvalidRequest("delivery must be a string")
        clean = delivery.strip()
        if not clean:
            return None
        if len(clean) > MAX_DELIVERY_CHARS:
            raise InvalidRequest(f"delivery exceeds {MAX_DELIVERY_CHARS} characters")
        return clean

    def _load_asset(self, voice_key: Any):
        if not isinstance(voice_key, str):
            raise InvalidRequest("voice_key must be a string")
        try:
            validate_key(voice_key)
        except ValueError as exc:
            raise InvalidRequest(f"invalid voice key {voice_key!r}") from exc
        try:
            return load_voice(voice_key)
        except FileNotFoundError as exc:
            raise UnknownVoice(f"no voice asset for key {voice_key!r}") from exc
        except Exception as exc:
            # The key resolves to a directory, but its manifest is unreadable: damaged
            # asset, not an unknown voice (Plan/0003 §2.1, 503 row).
            raise EngineUnavailable(f"voice asset {voice_key!r} is unreadable") from exc

    # -- worker-thread rendering ----------------------------------------------------------

    def _render(
        self,
        backend: Backend,
        model: Any,
        asset: VoiceAsset,
        segments: list[str],
        delivery: str | None,
    ) -> bytes:
        results: list[AudioResult] = []
        for position, segment in enumerate(segments, start=1):
            started = time.perf_counter()
            try:
                result = backend.synthesize(model, asset, segment, delivery)
            except TtsError:
                raise  # the backend already speaks the port's error vocabulary
            except Exception as exc:
                raise SynthesisFailed(
                    f"engine {backend.engine!r} failed on segment {position}/{len(segments)}"
                ) from exc
            self._check_result(result, position)
            frames = len(result.samples)
            audio_s = frames / float(result.sample_rate)
            infer_s = time.perf_counter() - started
            logger.info(
                "segment %d/%d chars=%d frames=%d infer=%.2fs rtf=%.3f",
                position,
                len(segments),
                len(segment),
                frames,
                infer_s,
                infer_s / audio_s if audio_s > 0 else 0.0,
            )
            results.append(result)
        samples, sample_rate = self._assemble(results)
        return self._encode_wav(samples, sample_rate)

    def _check_result(self, result: AudioResult, position: int) -> None:
        """The M2 objective structure gate: non-empty, finite, sane rate and channels."""
        import numpy as np

        samples = np.asarray(result.samples)
        if samples.size == 0:
            raise SynthesisFailed(f"segment {position} produced no samples")
        if samples.ndim > 2:
            raise SynthesisFailed(f"segment {position} has unsupported shape {samples.shape}")
        if not bool(np.isfinite(samples).all()):
            raise SynthesisFailed(f"segment {position} contains non-finite samples")
        channels = _channel_count(samples)
        if channels not in VALID_CHANNELS:
            raise SynthesisFailed(f"segment {position} has {channels} channels")
        rate = _sample_rate(result.sample_rate)
        if rate is None or not 0 < rate <= MAX_SAMPLE_RATE:
            raise SynthesisFailed(f"segment {position} reports sample rate {result.sample_rate!r}")

    def _assemble(self, results: list[AudioResult]):
        """Join segments in order with the fixed silence gap; consistency is enforced."""
        import numpy as np

        first_samples = np.asarray(results[0].samples)
        sample_rate = int(results[0].sample_rate)
        channels = _channel_count(first_samples)
        pieces: list[Any] = []
        for position, result in enumerate(results, start=1):
            samples = np.asarray(result.samples)
            if int(result.sample_rate) != sample_rate:
                raise SynthesisFailed("segments disagree on sample rate")
            if _channel_count(samples) != channels:
                raise SynthesisFailed("segments disagree on channel count")
            if position > 1:
                gap_frames = int(sample_rate * GAP_MS / 1000)
                shape = (gap_frames, channels) if samples.ndim == 2 else (gap_frames,)
                pieces.append(np.zeros(shape, dtype=samples.dtype))
            pieces.append(samples)
        merged = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        return merged, sample_rate

    def _encode_wav(self, samples: Any, sample_rate: int) -> bytes:
        import soundfile as sf

        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format="WAV")
        data = buffer.getvalue()
        try:
            info = sf.info(io.BytesIO(data))
        except Exception as exc:
            raise SynthesisFailed("final WAV is not readable") from exc
        if not data or info.frames <= 0 or int(info.samplerate) != sample_rate:
            raise SynthesisFailed("final WAV failed the structural check")
        return data


def _channel_count(samples) -> int:
    return int(samples.shape[1]) if samples.ndim == 2 else 1


def _sample_rate(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, numbers.Real):
        return None
    rate = int(raw)
    return rate if rate == raw else None
