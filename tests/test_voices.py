"""Voice asset store: canonical voices, their clips and manifests. No GPU involved."""
import os
import tempfile
import unittest
from pathlib import Path

from exocore_tts import voices


class VoiceStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root)
        self.clip = self.root / "cand_0001.wav"
        self.clip.write_bytes(b"RIFF____WAVEfmt ")

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous
        self._tmp.cleanup()

    def _asset(self, key: str = "sandro_v1", **overrides) -> voices.VoiceAsset:
        payload = {
            "key": key,
            "display_name": "Sandro",
            "baseline_instruction": "低沉克制",
            "prompt_text": "把茶喝了，再跟我说话。",
            "generation_defaults": {"cfg_value": 2.0, "inference_timesteps": 10},
            "source": {"candidate_id": "cand_0001"},
        }
        payload.update(overrides)
        return voices.VoiceAsset(**payload)

    def test_save_then_load_round_trips_every_field(self):
        voices.save_voice(self._asset(), self.clip)
        loaded = voices.load_voice("sandro_v1")
        self.assertEqual(loaded.display_name, "Sandro")
        self.assertEqual(loaded.engine, "voxcpm2")
        self.assertEqual(loaded.prompt_text, "把茶喝了，再跟我说话。")
        self.assertEqual(loaded.generation_defaults["cfg_value"], 2.0)
        self.assertEqual(loaded.source["candidate_id"], "cand_0001")

    def test_saved_clip_is_a_copy_of_the_candidate(self):
        target_dir = voices.save_voice(self._asset(), self.clip)
        stored = target_dir / voices.DEFAULT_REFERENCE_CLIP
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_bytes(), self.clip.read_bytes())
        self.assertEqual(voices.reference_path("sandro_v1"), stored)

    def test_existing_voice_is_not_silently_replaced(self):
        voices.save_voice(self._asset(), self.clip)
        with self.assertRaises(FileExistsError):
            voices.save_voice(self._asset(display_name="Other"), self.clip)
        # the first freeze survives the refused overwrite
        self.assertEqual(voices.load_voice("sandro_v1").display_name, "Sandro")

    def test_force_replaces_an_existing_voice(self):
        voices.save_voice(self._asset(), self.clip)
        voices.save_voice(self._asset(display_name="Sandro v2"), self.clip, force=True)
        self.assertEqual(voices.load_voice("sandro_v1").display_name, "Sandro v2")

    def test_missing_clip_creates_no_voice_directory(self):
        with self.assertRaises(FileNotFoundError):
            voices.save_voice(self._asset(), self.root / "nope.wav")
        self.assertFalse((self.root / "sandro_v1").exists())

    def test_invalid_keys_are_refused(self):
        for bad in ("Sandro", "sandro-v1", "sandro v1", "", "9sand ro"):
            with self.assertRaises(ValueError):
                voices.validate_key(bad)

    def test_list_skips_directories_without_a_manifest(self):
        voices.save_voice(self._asset(key="a_voice"), self.clip)
        (self.root / "half_written").mkdir()
        self.assertEqual([asset.key for asset in voices.list_voices()], ["a_voice"])

    def test_list_is_sorted_and_empty_when_root_missing(self):
        self.assertEqual(voices.list_voices(), [])
        voices.save_voice(self._asset(key="b_voice"), self.clip)
        voices.save_voice(self._asset(key="a_voice"), self.clip)
        self.assertEqual([asset.key for asset in voices.list_voices()], ["a_voice", "b_voice"])

    def test_unknown_voice_raises(self):
        with self.assertRaises(FileNotFoundError):
            voices.load_voice("never_made")


if __name__ == "__main__":
    unittest.main()
