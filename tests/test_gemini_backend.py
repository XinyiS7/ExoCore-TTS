"""Cloud engine seam: managed asset identity, exactly one paid render, bounded failures.

Everything here is offline -- the provider client is an injected double, the voice assets
live in a per-test temporary root and no key is ever read. What these tests prove is the
CP-G1 half of Plan/0004; the live capability gate (CP-G2) is the only thing that speaks to
the real provider.
"""
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from exocore_tts import voices
from exocore_tts.backends import default_backends
from exocore_tts.backends.gemini import GeminiBackend
from exocore_tts.cloud import CloudError
from exocore_tts.config import DaemonConfig
from exocore_tts.errors import EngineUnavailable, InvalidRequest, SynthesisFailed
from exocore_tts.server import create_app
from exocore_tts.service import MAX_DELIVERY_CHARS, TtsService

REPO_ROOT = Path(__file__).resolve().parents[1]
MANAGED_KEY = "sandro_gemini_v1"
MANAGED_REFERENCE = {"kind": "name", "value": "Ale 2.5 2"}

# Injected load failures log on purpose; test output stays readable without them.
logging.getLogger("exocore_tts").addHandler(logging.NullHandler())


def wav_bytes(text: str, *, sample_rate: int = 24000, frames_per_char: int = 16) -> bytes:
    """Deterministic stand-in for one provider render."""
    frames = max(1, len(text) * frames_per_char)
    timeline = np.arange(frames, dtype=np.float64) / sample_rate
    samples = (np.sin(2.0 * np.pi * 220.0 * timeline) * 0.05).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, samples, sample_rate, format="WAV")
    return buffer.getvalue()


class FakeCloudClient:
    """Offline double for `CloudVoiceClient`: records each call, never touches a network."""

    def __init__(
        self,
        *,
        payload: bytes | None = None,
        error: Exception | None = None,
        sample_rate: int = 24000,
        frames_per_char: int = 16,
    ) -> None:
        self.payload = payload
        self.error = error
        self.sample_rate = sample_rate
        self.frames_per_char = frames_per_char
        self.calls: list[dict] = []

    def render(self, text, *, voice="", prebuilt="", style="", temperature=1.0):
        self.calls.append({"text": text, "voice": voice, "prebuilt": prebuilt, "style": style})
        if self.error is not None:
            raise self.error
        if self.payload is not None:
            return self.payload
        return wav_bytes(text, sample_rate=self.sample_rate, frames_per_char=self.frames_per_char)


class CloudTestCase(unittest.TestCase):
    """Shared wiring: a temporary voice root, one offline client and a service over it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous_voice_root = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root)
        self.addCleanup(self._restore_environment)
        self.client = FakeCloudClient()

    def _restore_environment(self):
        if self._previous_voice_root is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous_voice_root
        self._tmp.cleanup()

    def add_cloud_voice(self, key=MANAGED_KEY, cloud_voice=None, engine="gemini", **overrides):
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": key,
            "display_name": key,
            "engine": engine,
            "cloud_voice": MANAGED_REFERENCE if cloud_voice is None else cloud_voice,
        }
        payload.update(overrides)
        (directory / voices.VOICE_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return key

    def make_service(self, *, client=None, client_factory=None):
        if client_factory is None:
            target = self.client if client is None else client
            client_factory = lambda: target  # noqa: E731 - a one-line test double factory
        service = TtsService(
            {"gemini": GeminiBackend(client_factory=client_factory)},
            max_text_chars=600,
            idle_unload_seconds=0.0,
        )
        self.addCleanup(service.close)
        return service

    def make_http_client(self, service):
        config = DaemonConfig(
            host="127.0.0.1",
            port=8769,
            token="",
            idle_unload_seconds=0,
            max_text_chars=600,
        )
        return TestClient(create_app(config, service))


class RegistryTests(CloudTestCase):
    def test_the_daemon_serves_both_engines(self):
        self.assertEqual(sorted(default_backends()), ["gemini", "voxcpm2"])

    def test_constructing_the_registry_imports_no_sdk_and_no_heavy_stack(self):
        code = (
            "import sys; from exocore_tts.backends import default_backends; default_backends(); "
            "leaked = sorted(name for name in sys.modules "
            "if name.split('.')[0] in ('torch', 'voxcpm', 'google')); "
            "assert not leaked, leaked"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class BackendContractTests(CloudTestCase):
    def test_the_backend_is_delivery_capable_and_keeps_the_text_whole(self):
        backend = GeminiBackend()
        self.assertTrue(backend.supports_delivery())
        text = "第一句。第二句。" * 30
        self.assertEqual(backend.plan_segments(text), [text])

    def test_the_call_shape_matches_the_real_provider_client(self):
        import inspect

        from exocore_tts.cloud import CloudVoiceClient

        # Constructing the client performs no call and reads no key: this is a shape check.
        signature = inspect.signature(CloudVoiceClient(api_key="offline-shape-check").render)
        for kind in voices.CLOUD_VOICE_KINDS:
            reference = {"voice": "voice_x"} if kind == "id" else {"prebuilt": "Some Name"}
            with self.subTest(kind=kind):
                signature.bind("文本", style="低沉", **reference)

    def test_the_default_registry_fails_closed_when_no_key_is_reachable(self):
        """The unedited production wiring must never be able to reach the network here."""
        from unittest import mock

        self.add_cloud_voice()
        unreachable = {"GEMINI_API_KEY": "", "EXOCORE_TTS_DOTENV": str(self.root / "absent.env")}
        with mock.patch.dict(os.environ, unreachable):
            service = TtsService(
                {"gemini": GeminiBackend()}, max_text_chars=600, idle_unload_seconds=0.0
            )
            self.addCleanup(service.close)
            with self.assertRaises(EngineUnavailable):
                service.synthesize(text="你好。", voice_key=MANAGED_KEY)

    def test_local_engines_keep_the_local_split(self):
        from exocore_tts.backends.fake import FakeBackend
        from exocore_tts.backends.voxcpm2 import VoxCpm2Backend

        text = "第一句。" * 40
        for backend in (FakeBackend(), VoxCpm2Backend()):
            with self.subTest(engine=backend.engine):
                segments = backend.plan_segments(text)
                self.assertGreater(len(segments), 1)
                self.assertEqual("".join(segments).replace(" ", ""), text.replace(" ", ""))


class AssetGateTests(CloudTestCase):
    def test_asset_without_a_reference_is_refused_before_any_provider_call(self):
        self.add_cloud_voice(cloud_voice={})
        service = self.make_service()
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="你好。", voice_key=MANAGED_KEY)
        self.assertEqual(self.client.calls, [])

    def test_every_malformed_reference_shape_is_refused_before_any_provider_call(self):
        for bad in (
            {"kind": "prebuilt", "value": "Kore"},
            {"kind": "name", "value": " "},
            "Ale 2.5 2",
            {"value": "Ale 2.5 2"},
        ):
            with self.subTest(bad=bad):
                self.add_cloud_voice(cloud_voice=bad)
                service = self.make_service()
                with self.assertRaises(EngineUnavailable):
                    service.synthesize(text="你好。", voice_key=MANAGED_KEY)
        self.assertEqual(self.client.calls, [])

    def test_missing_key_fails_closed_and_leaves_the_engine_cold(self):
        def failing_factory():
            raise CloudError("no GEMINI_API_KEY in the environment or in the env file")

        self.add_cloud_voice()
        service = self.make_service(client_factory=failing_factory)
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="你好。", voice_key=MANAGED_KEY)
        self.assertEqual(service.health_state(), "cold")
        self.assertEqual(self.client.calls, [])

    def test_cloud_asset_without_a_key_is_unknown_voice_not_a_cloud_call(self):
        service = self.make_service()
        with self.assertRaises(Exception) as caught:
            service.synthesize(text="你好。", voice_key="never_made")
        self.assertEqual(type(caught.exception).__name__, "UnknownVoice")
        self.assertEqual(self.client.calls, [])


class BaselineStyleTests(CloudTestCase):
    """Addendum A2: the asset's baseline style is the default; delivery overrides verbatim."""

    BASELINE = "Style: " + ("deep resonant baritone " * 3).strip()

    def test_baseline_is_the_default_style_when_delivery_is_absent(self):
        self.add_cloud_voice(baseline_style=self.BASELINE)
        service = self.make_service()
        wav = service.synthesize(text="站住。", voice_key=MANAGED_KEY)
        self.assertGreater(len(wav), 44)
        self.assertEqual(self.client.calls[0]["style"], self.BASELINE)
        # the baseline is a direction, never spoken material
        self.assertEqual(self.client.calls[0]["text"], "站住。")

    def test_delivery_overrides_the_baseline_verbatim(self):
        self.add_cloud_voice(baseline_style=self.BASELINE)
        service = self.make_service()
        service.synthesize(text="站住。", voice_key=MANAGED_KEY, delivery="closer, whispered")
        # no prefix added, nothing normalized (Addendum A2 verbatim rule)
        self.assertEqual(self.client.calls[0]["style"], "closer, whispered")

    def test_asset_without_a_baseline_keeps_the_original_behaviour(self):
        self.add_cloud_voice()  # no baseline_style field at all
        service = self.make_service()
        service.synthesize(text="站住。", voice_key=MANAGED_KEY)
        self.assertEqual(self.client.calls[0]["style"], "")

    def test_unusable_baseline_shape_is_refused_before_the_provider(self):
        self.add_cloud_voice(baseline_style=7)
        service = self.make_service()
        with self.assertRaises(EngineUnavailable):
            service.synthesize(text="站住。", voice_key=MANAGED_KEY)
        self.assertEqual(self.client.calls, [])

    def test_wire_request_without_delivery_sends_the_baseline(self):
        self.add_cloud_voice(baseline_style=self.BASELINE)
        http = self.make_http_client(self.make_service())
        response = http.post("/tts", json={"text": "站住。", "voice_key": MANAGED_KEY})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.calls[0]["style"], self.BASELINE)


class SinglePaidRenderTests(CloudTestCase):
    LONG_TEXT = ("这是一段足够长的文本，本地引擎会把它切成很多段；" * 12).strip()

    def test_the_whole_text_reaches_the_provider_exactly_once(self):
        self.add_cloud_voice()
        service = self.make_service()
        wav = service.synthesize(text=self.LONG_TEXT, voice_key=MANAGED_KEY)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0]["text"], self.LONG_TEXT)
        with sf.SoundFile(io.BytesIO(wav)) as handle:
            self.assertGreater(handle.frames, 0)
            self.assertEqual(handle.samplerate, 24000)

    def test_a_failing_render_is_never_retried(self):
        self.client.error = CloudError("provider refused (already scrubbed)")
        self.add_cloud_voice()
        service = self.make_service()
        with self.assertRaises(SynthesisFailed):
            service.synthesize(text="你好。", voice_key=MANAGED_KEY)
        self.assertEqual(len(self.client.calls), 1)

    def test_unreadable_provider_bytes_fail_closed_without_a_retry(self):
        self.client.payload = b"this is not audio"
        self.add_cloud_voice()
        service = self.make_service()
        with self.assertRaises(SynthesisFailed):
            service.synthesize(text="你好。", voice_key=MANAGED_KEY)
        self.assertEqual(len(self.client.calls), 1)

    def test_delivery_is_passed_verbatim_and_absent_stays_empty(self):
        self.add_cloud_voice()
        service = self.make_service()
        service.synthesize(text="站住。", voice_key=MANAGED_KEY)
        service.synthesize(text="站住。", voice_key=MANAGED_KEY, delivery="  低沉、克制  ")
        self.assertEqual(self.client.calls[0]["style"], "")
        self.assertEqual(self.client.calls[1]["style"], "低沉、克制")
        # the direction is never smuggled into the spoken text, and the voice slot is the
        # managed one -- named kind goes to the provider's voice_name, not the id slot
        self.assertEqual(self.client.calls[1]["text"], "站住。")
        self.assertEqual(self.client.calls[1]["prebuilt"], "Ale 2.5 2")
        self.assertEqual(self.client.calls[1]["voice"], "")

    def test_id_kind_uses_the_voice_resource_slot(self):
        self.add_cloud_voice(cloud_voice={"kind": "id", "value": "voice_nnvw5qprqmz7"})
        service = self.make_service()
        service.synthesize(text="站住。", voice_key=MANAGED_KEY)
        self.assertEqual(self.client.calls[0]["voice"], "voice_nnvw5qprqmz7")
        self.assertEqual(self.client.calls[0]["prebuilt"], "")

    def test_delivery_over_the_cap_is_refused_before_the_provider(self):
        self.add_cloud_voice()
        service = self.make_service()
        with self.assertRaises(InvalidRequest):
            service.synthesize(
                text="站住。", voice_key=MANAGED_KEY, delivery="x" * (MAX_DELIVERY_CHARS + 1)
            )
        self.assertEqual(self.client.calls, [])


class WireTests(CloudTestCase):
    def test_post_tts_returns_readable_wav_for_the_managed_asset(self):
        self.add_cloud_voice()
        http = self.make_http_client(self.make_service())
        response = http.post(
            "/tts",
            json={"text": "现在，去吃饭。", "voice_key": MANAGED_KEY, "delivery": "低沉"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        with sf.SoundFile(io.BytesIO(response.content)) as handle:
            self.assertGreater(handle.frames, 0)
        self.assertEqual(self.client.calls[0]["style"], "低沉")

    def test_provider_fields_are_not_part_of_the_wire(self):
        self.add_cloud_voice()
        http = self.make_http_client(self.make_service())
        for extra in ({"style": "低沉"}, {"model": "gemini-3.8-flash-tts"}, {"voice_id": "voice_x"}):
            with self.subTest(extra=extra):
                response = http.post(
                    "/tts", json={"text": "站住。", "voice_key": MANAGED_KEY, **extra}
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"error": "invalid_request"})
        self.assertEqual(self.client.calls, [])

    def test_unknown_voice_and_health_keep_their_frozen_bodies(self):
        http = self.make_http_client(self.make_service())
        response = http.post("/tts", json={"text": "站住。", "voice_key": "never_made"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "unknown_voice"})
        self.assertEqual(http.get("/health").json(), {"status": "ok", "state": "cold"})

    def test_missing_key_maps_to_the_bounded_503_body(self):
        def failing_factory():
            raise CloudError("no key")

        self.add_cloud_voice()
        http = self.make_http_client(self.make_service(client_factory=failing_factory))
        response = http.post("/tts", json={"text": "站住。", "voice_key": MANAGED_KEY})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "engine_unavailable"})

    def test_provider_failure_maps_to_the_bounded_500_body(self):
        self.client.error = CloudError("provider said no (already scrubbed)")
        self.add_cloud_voice()
        http = self.make_http_client(self.make_service())
        response = http.post("/tts", json={"text": "站住。", "voice_key": MANAGED_KEY})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"error": "synthesis_failed"})
        self.assertEqual(len(self.client.calls), 1)


if __name__ == "__main__":
    unittest.main()
