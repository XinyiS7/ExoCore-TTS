"""Service behaviour: validation, dispatch, segmentation, assembly and the structure gate.

Voice assets are written into a per-test temporary root; engines are fakes. No GPU, no net.
"""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import soundfile as sf

from exocore_tts import voices, voxcpm
from exocore_tts.backends.base import AudioResult
from exocore_tts.backends.fake import FakeBackend
from exocore_tts.backends.voxcpm2 import VoxCpm2Backend
from exocore_tts.errors import (
    DeliveryUnsupported,
    EngineUnavailable,
    InvalidRequest,
    SynthesisFailed,
    UnknownVoice,
)
from exocore_tts.service import GAP_MS, MAX_DELIVERY_CHARS, TtsService
from exocore_tts.text import segment_text

SAMPLES_PER_CHAR = 16


class StubBackend:
    """Minimal backend for structural-gate cases; returns exactly what the test says.

    With ``per_call=True``, ``samples`` and ``sample_rate`` are indexed by call number so a
    multi-segment request can return different facts per segment.
    """

    engine = "fake"

    def __init__(self, samples, sample_rate=24000, *, per_call=False):
        self._samples = samples
        self._sample_rate = sample_rate
        self._per_call = per_call
        self.calls = 0

    def _pick(self, value):
        return value[self.calls] if self._per_call else value

    def supports_delivery(self):
        return True

    def plan_segments(self, text):
        return segment_text(text)

    def check_asset(self, asset):
        return None

    def load(self):
        return object()

    def unload(self, model):
        return None

    def synthesize(self, model, asset, text, delivery):
        samples = self._pick(self._samples)
        sample_rate = self._pick(self._sample_rate)
        self.calls += 1
        return AudioResult(samples=samples, sample_rate=sample_rate)


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous_voice_root = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root)
        self.addCleanup(self._restore_environment)

    def _restore_environment(self):
        if self._previous_voice_root is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous_voice_root
        self._tmp.cleanup()

    def add_voice(self, key="probe", engine="fake", **overrides):
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "display_name": key, "engine": engine, "prompt_text": "参考句。"}
        payload.update(overrides)
        (directory / voices.VOICE_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return key

    def make_service(self, backend=None, *, max_text_chars=60, backends=None, **kwargs):
        backend = backend if backend is not None else FakeBackend()
        registry = backends if backends is not None else {"fake": backend}
        service = TtsService(
            registry, max_text_chars=max_text_chars, idle_unload_seconds=0.0, **kwargs
        )
        self.addCleanup(service.close)
        return service, backend

    def decode(self, audio: bytes):
        return sf.read(io.BytesIO(audio))


class RequestPathTests(ServiceTestCase):
    def test_text_is_split_and_every_segment_reaches_the_backend(self):
        service, backend = self.make_service()
        voice = self.add_voice()
        service.synthesize(text="  第一句。第二句！  ", voice_key=voice)
        self.assertEqual(backend.texts, ["第一句。", "第二句！"])
        self.assertEqual(backend.synth_count, 2)
        self.assertEqual(backend.asset_keys, [voice, voice])

    def test_delivery_reaches_every_segment_or_is_absent(self):
        service, backend = self.make_service()
        voice = self.add_voice()
        service.synthesize(text="第一句。第二句。", voice_key=voice, delivery="放慢一点，克制")
        self.assertEqual(backend.deliveries, ["放慢一点，克制", "放慢一点，克制"])
        backend.deliveries.clear()
        service.synthesize(text="一。", voice_key=voice, delivery="   ")
        self.assertEqual(backend.deliveries, [None])

    def test_unsupported_delivery_is_refused_before_any_engine_work(self):
        service, backend = self.make_service(FakeBackend(delivery_supported=False))
        voice = self.add_voice()
        with self.assertRaises(DeliveryUnsupported):
            service.synthesize(text="一。", voice_key=voice, delivery="耳语")
        self.assertEqual(backend.synth_count, 0)
        self.assertEqual(backend.load_count, 0)

    def test_segments_are_joined_with_the_fixed_silence_gap(self):
        service, _ = self.make_service()
        voice = self.add_voice()
        audio = service.synthesize(text="短。也短。", voice_key=voice)
        samples, rate = self.decode(audio)
        first = len("短。") * SAMPLES_PER_CHAR
        second = len("也短。") * SAMPLES_PER_CHAR
        gap = int(rate * GAP_MS / 1000)
        self.assertEqual(rate, 24000)
        self.assertEqual(len(samples), first + second + gap)
        self.assertTrue(all(sample == 0.0 for sample in samples[first : first + gap]))

    def test_a_single_segment_gets_no_trailing_gap(self):
        service, _ = self.make_service()
        voice = self.add_voice()
        samples, _ = self.decode(service.synthesize(text="短。", voice_key=voice))
        self.assertEqual(len(samples), len("短。") * SAMPLES_PER_CHAR)

    def test_any_failing_segment_fails_the_whole_request(self):
        service, backend = self.make_service(FakeBackend(fail_on_text="第二"))
        voice = self.add_voice()
        with self.assertRaises(SynthesisFailed):
            service.synthesize(text="第一句。第二句。", voice_key=voice)
        self.assertEqual(backend.synth_count, 2)  # it failed, it did not return a half WAV


class ValidationTests(ServiceTestCase):
    def test_text_guard_boundaries(self):
        service, _ = self.make_service(max_text_chars=10)
        voice = self.add_voice()
        for blank in ("", "   \n\t "):
            with self.assertRaises(InvalidRequest):
                service.synthesize(text=blank, voice_key=voice)
        service.synthesize(text="字" * 10, voice_key=voice)
        with self.assertRaises(InvalidRequest):
            service.synthesize(text="字" * 11, voice_key=voice)
        with self.assertRaises(InvalidRequest):
            service.synthesize(text=123, voice_key=voice)

    def test_delivery_guard_boundaries(self):
        service, _ = self.make_service()
        voice = self.add_voice()
        service.synthesize(text="一。", voice_key=voice, delivery="d" * MAX_DELIVERY_CHARS)
        with self.assertRaises(InvalidRequest):
            service.synthesize(text="一。", voice_key=voice, delivery="d" * (MAX_DELIVERY_CHARS + 1))
        with self.assertRaises(InvalidRequest):
            service.synthesize(text="一。", voice_key=voice, delivery=5)

    def test_voice_lookup_errors(self):
        service, _ = self.make_service()
        self.add_voice()
        self.add_voice(key="cloudy", engine="cloud")
        with self.assertRaises(InvalidRequest):
            service.synthesize(text="一。", voice_key="Probe V1")
        with self.assertRaises(UnknownVoice):
            service.synthesize(text="一。", voice_key="never_made")
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="一。", voice_key="cloudy")

    def test_corrupt_manifest_is_engine_unavailable(self):
        service, _ = self.make_service()
        broken = self.root / "broken"
        broken.mkdir()
        (broken / voices.VOICE_MANIFEST).write_text("{not json", encoding="utf-8")
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="一。", voice_key="broken")

    def test_backend_reported_asset_damage_maps_to_engine_unavailable(self):
        # The reference clip is only checkable by the real engine, so the voxcpm2 backend
        # raises this class itself; here we pin that the service passes it through as 503.
        class BrokenAssetBackend(StubBackend):
            def synthesize(self, model, asset, text, delivery):
                raise EngineUnavailable("reference clip is missing")

        service, _ = self.make_service(BrokenAssetBackend(samples=[0.0]))
        voice = self.add_voice()
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="一。", voice_key=voice)

    def test_service_without_backends_answers_no_voice(self):
        service, _ = self.make_service(backends={})
        self.assertEqual(service.health_state(), "cold")
        voice = self.add_voice()
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="一。", voice_key=voice)

    def test_registry_key_must_match_the_backend_engine(self):
        with self.assertRaises(ValueError):
            TtsService({"other": FakeBackend()}, max_text_chars=10)


class StructureGateTests(ServiceTestCase):
    def run_with_backend(self, backend, text="一。"):
        service, _ = self.make_service(backend)
        voice = self.add_voice()
        return service.synthesize(text=text, voice_key=voice)

    def test_plain_lists_are_accepted_as_samples(self):
        audio = self.run_with_backend(StubBackend(samples=[0.0, 0.1, -0.1, 0.0]))
        samples, rate = self.decode(audio)
        self.assertEqual(rate, 24000)
        self.assertEqual(len(samples), 4)

    def test_empty_samples_are_rejected(self):
        with self.assertRaises(SynthesisFailed):
            self.run_with_backend(StubBackend(samples=[]))

    def test_non_finite_samples_are_rejected(self):
        with self.assertRaises(SynthesisFailed):
            self.run_with_backend(StubBackend(samples=[0.0, float("nan")]))

    def test_invalid_sample_rate_is_rejected(self):
        with self.assertRaises(SynthesisFailed):
            self.run_with_backend(StubBackend(samples=[0.0, 0.1], sample_rate=0))

    def test_invalid_channel_count_is_rejected(self):
        three_channels = [[0.0, 0.0, 0.0], [0.1, 0.1, 0.1]]
        with self.assertRaises(SynthesisFailed):
            self.run_with_backend(StubBackend(samples=three_channels))

    def test_mismatched_rates_across_segments_are_rejected(self):
        backend = StubBackend(
            samples=[[0.0, 0.1], [0.0, 0.1]], sample_rate=[24000, 16000], per_call=True
        )
        with self.assertRaises(SynthesisFailed):
            self.run_with_backend(backend, text="第一句。第二句。")


class VoxCpm2ServiceTests(ServiceTestCase):
    """The real engine class wired through the service, still without touching the GPU."""

    def make_vox_service(self):
        service = TtsService(
            {"voxcpm2": VoxCpm2Backend()}, max_text_chars=60, idle_unload_seconds=0.0
        )
        self.addCleanup(service.close)
        return service

    def write_reference(self, key):
        sf.write(
            str(self.root / key / voices.DEFAULT_REFERENCE_CLIP),
            [0.0] * 24000,
            24000,
            format="WAV",
        )

    def test_missing_reference_clip_fails_before_any_model_load(self):
        service = self.make_vox_service()
        voice = self.add_voice("naked", engine="voxcpm2")
        with mock.patch.object(voxcpm, "load_model", side_effect=AssertionError("must not load")):
            with self.assertRaises(EngineUnavailable):
                service.synthesize(text="你好。", voice_key=voice)

    def test_delivery_is_refused_before_any_model_load(self):
        service = self.make_vox_service()
        voice = self.add_voice("voiced", engine="voxcpm2")
        self.write_reference(voice)
        with mock.patch.object(voxcpm, "load_model", side_effect=AssertionError("must not load")):
            with self.assertRaises(DeliveryUnsupported):
                service.synthesize(text="你好。", voice_key=voice, delivery="放慢一点")


class HealthStateTests(ServiceTestCase):
    def test_health_follows_the_engine_lifecycle(self):
        service, _ = self.make_service()
        self.assertEqual(service.health_state(), "cold")
        voice = self.add_voice()
        service.synthesize(text="一。", voice_key=voice)
        self.assertEqual(service.health_state(), "ready")


if __name__ == "__main__":
    unittest.main()
