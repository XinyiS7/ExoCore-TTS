"""Shared Vox low-level entry + the voxcpm2 backend translation.

No torch, no CUDA, no model: everything heavy is replaced by strict doubles -- a fake torch,
a fake VoxCPM class and a fake model whose `generate` records the exact kwargs it was handed.
"""
import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from exocore_tts import voxcpm
from exocore_tts.backends.voxcpm2 import SEED_CEILING, VoxCpm2Backend
from exocore_tts.errors import DeliveryUnsupported, EngineUnavailable
from exocore_tts.voices import DEFAULT_REFERENCE_CLIP, VoiceAsset


class FakeCuda:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.seeds: list[int] = []
        self.peak_resets = 0
        self.empty_cache_calls = 0

    def is_available(self) -> bool:
        return self.available

    def manual_seed_all(self, seed: int) -> None:
        self.seeds.append(seed)

    def reset_peak_memory_stats(self) -> None:
        self.peak_resets += 1

    def max_memory_allocated(self) -> int:
        return 64 * 1024 * 1024

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1

    def get_device_name(self, index: int) -> str:
        return "Fake GPU"


class FakeTorch:
    def __init__(self, cuda_available: bool = True) -> None:
        self.cuda = FakeCuda(cuda_available)
        self.seeds: list[int] = []
        self.inference_entries = 0

    def manual_seed(self, seed: int) -> None:
        self.seeds.append(seed)

    def inference_mode(self):
        self.inference_entries += 1
        return contextlib.nullcontext()


class FakeModel:
    """Records every generation call; returns silence of a known length."""

    def __init__(self, frames: int = 24000, sample_rate: int | None = None) -> None:
        if sample_rate is not None:
            self.sample_rate = sample_rate
        self.frames = frames
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return np.zeros(self.frames, dtype=np.float32)


class FakeVoxCpmClass:
    def __init__(self, model) -> None:
        self._model = model
        self.loaded: list[dict] = []

    def from_pretrained(self, model_id, *, load_denoiser=False, device=None):
        self.loaded.append({"model_id": model_id, "load_denoiser": load_denoiser, "device": device})
        return self._model


class LoadModelTests(unittest.TestCase):
    def load_with(self, model, *, cuda_available: bool = True):
        torch = FakeTorch(cuda_available)
        voxcpm_class = FakeVoxCpmClass(model)
        with mock.patch.object(voxcpm, "_runtime", return_value=(torch, voxcpm_class)):
            loaded = voxcpm.load_model("some/model", None)
        return loaded, torch, voxcpm_class

    def test_reads_the_sample_rate_from_the_model(self):
        loaded, _, voxcpm_class = self.load_with(FakeModel(sample_rate=24000))
        self.assertEqual(loaded.sample_rate, 24000)
        self.assertEqual(voxcpm_class.loaded[0]["model_id"], "some/model")
        self.assertFalse(voxcpm_class.loaded[0]["load_denoiser"])

    def test_reads_the_sample_rate_from_the_inner_tts_model(self):
        model = FakeModel()
        model.tts_model = FakeModel(sample_rate=16000)
        loaded, _, _ = self.load_with(model)
        self.assertEqual(loaded.sample_rate, 16000)

    def test_falls_back_when_the_model_exposes_no_sample_rate(self):
        loaded, _, _ = self.load_with(FakeModel())
        self.assertEqual(loaded.sample_rate, voxcpm.FALLBACK_SAMPLE_RATE)

    def test_refuses_to_load_without_cuda(self):
        with self.assertRaises(RuntimeError):
            self.load_with(FakeModel(sample_rate=24000), cuda_available=False)

    def test_release_drops_the_model_and_empties_the_device_cache(self):
        loaded, torch, _ = self.load_with(FakeModel(sample_rate=24000))
        loaded.release()
        self.assertIsNone(loaded.model)
        self.assertEqual(torch.cuda.empty_cache_calls, 1)

    def test_release_on_a_cpu_only_machine_does_not_touch_the_cache(self):
        loaded, torch, _ = self.load_with(FakeModel(sample_rate=24000))
        torch.cuda.available = False
        loaded.release()
        self.assertEqual(torch.cuda.empty_cache_calls, 0)


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.torch = FakeTorch()
        self.model = FakeModel(frames=24000)
        self.loaded = voxcpm.LoadedModel(model=self.model, sample_rate=24000, torch=self.torch)

    def generate(self, **overrides):
        kwargs = {
            "text": "你好。",
            "cfg_value": 3.5,
            "inference_timesteps": 16,
            "seed": 7,
            "reference_wav": "ref.wav",
            "prompt_text": "你好。",
        }
        kwargs.update(overrides)
        return voxcpm.generate(self.loaded, **kwargs)

    def test_clone_kwargs_pair_the_reference_with_its_transcript(self):
        self.generate()
        kwargs = self.model.calls[0]
        self.assertEqual(kwargs["text"], "你好。")
        self.assertEqual(kwargs["cfg_value"], 3.5)
        self.assertEqual(kwargs["inference_timesteps"], 16)
        self.assertEqual(kwargs["reference_wav_path"], "ref.wav")
        self.assertEqual(kwargs["prompt_wav_path"], "ref.wav")
        self.assertEqual(kwargs["prompt_text"], "你好。")

    def test_absent_reference_or_transcript_keeps_the_kwargs_minimal(self):
        self.generate(reference_wav="", prompt_text="")
        self.assertEqual(set(self.model.calls[0]), {"text", "cfg_value", "inference_timesteps"})
        self.generate(reference_wav="ref.wav", prompt_text="")
        self.assertNotIn("prompt_text", self.model.calls[1])
        self.assertEqual(self.model.calls[1]["reference_wav_path"], "ref.wav")

    def test_seed_is_applied_and_the_peak_counter_is_reset(self):
        self.generate(seed=99)
        self.assertEqual(self.torch.seeds, [99])
        self.assertEqual(self.torch.cuda.seeds, [99])
        self.assertEqual(self.torch.cuda.peak_resets, 1)
        self.assertEqual(self.torch.inference_entries, 1)

    def test_facts_keep_the_manifest_shape(self):
        _, facts = self.generate()
        self.assertEqual(
            set(facts), {"infer_s", "audio_s", "rtf", "peak_allocated_mb", "sample_rate"}
        )
        self.assertEqual(facts["audio_s"], 1.0)
        self.assertEqual(facts["sample_rate"], 24000)
        self.assertEqual(facts["peak_allocated_mb"], 64.0)
        self.assertGreaterEqual(facts["infer_s"], 0.0)
        self.assertEqual(facts["rtf"], round(facts["infer_s"] / facts["audio_s"], 3))


class VoxCpm2BackendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous_voice_root = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root)
        self.addCleanup(self._restore_environment)
        self.backend = VoxCpm2Backend()
        self.asset = self.make_asset()

    def _restore_environment(self):
        if self._previous_voice_root is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous_voice_root
        self._tmp.cleanup()

    def make_asset(self, key: str = "sandro_v1", *, clip: bool = True, defaults=None):
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        if clip:
            sf.write(
                str(directory / DEFAULT_REFERENCE_CLIP),
                np.zeros(24000, dtype=np.float32),
                24000,
                format="WAV",
            )
        return VoiceAsset(
            key=key,
            engine="voxcpm2",
            prompt_text="参考句。",
            generation_defaults=(
                defaults
                if defaults is not None
                else {"cfg_value": 3.5, "inference_timesteps": 16}
            ),
        )

    def test_delivery_is_refused_until_the_listening_probe_passes(self):
        self.assertFalse(self.backend.supports_delivery())
        loaded = voxcpm.LoadedModel(model=object(), sample_rate=24000, torch=None)
        with self.assertRaises(DeliveryUnsupported):
            self.backend.synthesize(loaded, self.asset, "你好。", "放慢一点")

    def test_a_valid_asset_passes_the_preflight(self):
        self.backend.check_asset(self.asset)

    def test_missing_reference_clip_is_engine_unavailable(self):
        with self.assertRaises(EngineUnavailable):
            self.backend.check_asset(self.make_asset("naked", clip=False))

    def test_corrupt_reference_clip_is_engine_unavailable(self):
        asset = self.make_asset("junk")
        (self.root / "junk" / DEFAULT_REFERENCE_CLIP).write_bytes(b"this is not audio")
        with self.assertRaises(EngineUnavailable):
            self.backend.check_asset(asset)

    def test_unusable_generation_defaults_are_engine_unavailable(self):
        for index, defaults in enumerate(
            (
                {"cfg_value": "hot", "inference_timesteps": 16},
                {"cfg_value": 3.5, "inference_timesteps": 0},
                {"cfg_value": -1.0, "inference_timesteps": 16},
                {"cfg_value": 3.5, "inference_timesteps": 16.5},
            )
        ):
            asset = self.make_asset(f"bad_{index}", defaults=defaults)
            with self.assertRaises(EngineUnavailable, msg=str(defaults)):
                self.backend.check_asset(asset)

    def test_synthesize_translates_the_asset_and_owns_the_seed(self):
        loaded = voxcpm.LoadedModel(model=object(), sample_rate=24000, torch=None)
        captured: list[dict] = []

        def fake_generate(handle, **kwargs):
            captured.append(kwargs)
            return np.zeros(24000, dtype=np.float32), {
                "infer_s": 0.1,
                "audio_s": 1.0,
                "rtf": 0.1,
                "peak_allocated_mb": 1.0,
                "sample_rate": 24000,
            }

        with mock.patch.object(voxcpm, "generate", side_effect=fake_generate):
            first = self.backend.synthesize(loaded, self.asset, "你好。", None)
            with self.assertRaises(TypeError):
                # no caller-controlled seed exists on the surface
                self.backend.synthesize(loaded, self.asset, "你好。", None, seed=5)
        self.assertEqual(first.sample_rate, 24000)
        self.assertEqual(len(first.samples), 24000)
        self.assertEqual(len(captured), 1)
        kwargs = captured[0]
        self.assertEqual(kwargs["text"], "你好。")
        self.assertEqual(kwargs["cfg_value"], 3.5)
        self.assertEqual(kwargs["inference_timesteps"], 16)
        self.assertEqual(kwargs["prompt_text"], "参考句。")
        self.assertTrue(kwargs["reference_wav"].endswith(DEFAULT_REFERENCE_CLIP))
        self.assertIsInstance(kwargs["seed"], int)
        self.assertGreaterEqual(kwargs["seed"], 0)
        self.assertLess(kwargs["seed"], SEED_CEILING)


class ImportHygieneTests(unittest.TestCase):
    def test_the_daemon_never_imports_the_heavy_stack_on_start_up(self):
        code = (
            "import sys; import exocore_tts.server, exocore_tts.backends; "
            "exocore_tts.backends.default_backends(); "
            "heavy = sorted(name for name in sys.modules "
            "if name.split('.')[0] in ('torch', 'voxcpm')); "
            "assert not heavy, heavy"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
