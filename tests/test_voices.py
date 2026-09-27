"""Voice asset store: canonical voices, their clips and manifests. No GPU involved."""
import json
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

    # -- cloud assets (Plan/0004 §2.2) -------------------------------------------------

    def test_cloud_voice_round_trips_through_the_managed_writer(self):
        asset = self._asset(
            key="sandro_gemini_v1",
            engine="gemini",
            cloud_voice={"kind": "name", "value": "Ale 2.5 2"},
        )
        target = voices.save_cloud_voice(asset)
        self.assertEqual(target, self.root / "sandro_gemini_v1")
        loaded = voices.load_voice("sandro_gemini_v1")
        self.assertEqual(loaded.engine, "gemini")
        self.assertEqual(loaded.cloud_voice, {"kind": "name", "value": "Ale 2.5 2"})
        self.assertEqual(voices.cloud_voice_ref(loaded), ("name", "Ale 2.5 2"))
        # a cloud asset needs no local clip at all
        self.assertFalse((target / voices.DEFAULT_REFERENCE_CLIP).exists())

    def test_cloud_registration_never_replaces_a_manifest_silently(self):
        first = {"kind": "id", "value": "voice_a"}
        second = {"kind": "id", "value": "voice_b"}
        voices.save_cloud_voice(self._asset(key="sandro_gemini_v1", engine="gemini", cloud_voice=first))
        with self.assertRaises(FileExistsError):
            voices.save_cloud_voice(
                self._asset(key="sandro_gemini_v1", engine="gemini", cloud_voice=second)
            )
        self.assertEqual(voices.load_voice("sandro_gemini_v1").cloud_voice, first)
        voices.save_cloud_voice(
            self._asset(key="sandro_gemini_v1", engine="gemini", cloud_voice=second), force=True
        )
        self.assertEqual(voices.load_voice("sandro_gemini_v1").cloud_voice, second)

    def test_cloud_reference_shapes_are_enforced(self):
        def asset(payload):
            return self._asset(key="c1", engine="gemini", cloud_voice=payload)

        self.assertEqual(
            voices.cloud_voice_ref(asset({"kind": "id", "value": "  voice_x  "})),
            ("id", "voice_x"),
        )
        for bad in (
            {},
            {"kind": "prebuilt", "value": "Kore"},
            {"kind": "name", "value": "   "},
            {"kind": "name"},
            {"kind": "name", "value": 7},
            "Ale 2.5 2",
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                voices.cloud_voice_ref(asset(bad))

    def test_cloud_baseline_style_round_trips_and_is_validated(self):
        asset = self._asset(
            key="sandro_gemini_v1",
            engine="gemini",
            cloud_voice={"kind": "id", "value": "voice_x"},
            baseline_style="  Style: deep resonant baritone  ",
        )
        voices.save_cloud_voice(asset)
        loaded = voices.load_voice("sandro_gemini_v1")
        # stored verbatim apart from the surrounding whitespace -- no prefix is ever implied
        self.assertEqual(loaded.baseline_style, "Style: deep resonant baritone")

        with self.assertRaises(ValueError):
            voices.save_cloud_voice(
                self._asset(
                    key="c2",
                    engine="gemini",
                    cloud_voice={"kind": "id", "value": "voice_x"},
                    baseline_style=5,
                )
            )
        self.assertFalse((self.root / "c2").exists())
        # assets that never set the field keep the old shape
        self.assertEqual(self._asset(key="old_vox").baseline_style, "")

    def test_cloud_writer_persists_no_unusable_reference(self):
        with self.assertRaises(ValueError):
            voices.save_cloud_voice(
                self._asset(key="c1", engine="gemini", cloud_voice={"kind": "prebuilt", "value": "Kore"})
            )
        self.assertFalse((self.root / "c1").exists())

    def test_existing_vox_manifests_keep_an_empty_cloud_field(self):
        voices.save_voice(self._asset(), self.clip)
        self.assertEqual(voices.load_voice("sandro_v1").cloud_voice, {})
        self.assertEqual(self._asset().cloud_voice, {})

    def test_unknown_manifest_fields_are_still_ignored(self):
        directory = self.root / "v1"
        directory.mkdir()
        (directory / voices.VOICE_MANIFEST).write_text(
            json.dumps({"key": "v1", "engine": "voxcpm2", "surprise": {"nested": True}}),
            encoding="utf-8",
        )
        self.assertEqual(voices.load_voice("v1").cloud_voice, {})


if __name__ == "__main__":
    unittest.main()
