"""Casting plan, manifest and freeze behaviour. Runs without torch, CUDA or the voice model."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from exocore_tts import casting, voices


class PlanTests(unittest.TestCase):
    def test_design_plan_expands_designs_lines_and_repeats(self):
        plan = casting.build_plan(lines=["一", "二", "三"], designs=["A", "B"], repeats=2)
        self.assertEqual(len(plan), 12)
        self.assertEqual(plan[0].candidate_id, "cand_0001")
        self.assertEqual(plan[-1].candidate_id, "cand_0012")
        # repeats of one (design, line) stay adjacent: the same draw repeated, not reordered
        self.assertEqual([spec.design for spec in plan[:2]], ["A", "A"])
        self.assertEqual([spec.line for spec in plan[:2]], ["一", "一"])
        self.assertEqual(plan[2].line, "二")
        self.assertEqual(plan[6].design, "B")

    def test_design_text_is_wrapped_in_parentheses_ahead_of_the_line(self):
        plan = casting.build_plan(lines=["别躲。"], designs=["低沉男声"])
        self.assertEqual(plan[0].model_text, "(低沉男声)别躲。")
        self.assertEqual(plan[0].line, "别躲。")

    def test_seeds_are_unique_and_derived_from_the_seed_base(self):
        plan = casting.build_plan(lines=["一"], designs=["A"], repeats=3, seed_base=500)
        self.assertEqual([spec.seed for spec in plan], [500, 501, 502])

    def test_design_mode_needs_a_description(self):
        with self.assertRaises(ValueError):
            casting.build_plan(lines=["一"], designs=["  "])

    def test_clone_plan_carries_the_reference_and_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "ref.wav"
            clip.write_bytes(b"RIFF____WAVEfmt ")
            plan = casting.build_plan(
                lines=["一", "二"],
                mode=casting.MODE_CLONE,
                reference_wav=str(clip),
                prompt_text="被朗读的那句",
            )
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0].mode, casting.MODE_CLONE)
        self.assertEqual(plan[0].model_text, "一")
        self.assertEqual(plan[0].prompt_text, "被朗读的那句")
        self.assertTrue(Path(plan[0].reference_wav).is_absolute())

    def test_clone_mode_needs_an_existing_audio_file(self):
        with self.assertRaises(ValueError):
            casting.build_plan(lines=["一"], mode=casting.MODE_CLONE, reference_wav="missing.wav")

    def test_modes_refuse_each_others_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "ref.wav"
            clip.write_bytes(b"RIFF____WAVEfmt ")
            with self.assertRaises(ValueError):
                casting.build_plan(lines=["一"], mode=casting.MODE_CLONE, reference_wav=str(clip), designs=["A"])
            with self.assertRaises(ValueError):
                casting.build_plan(lines=["一"], designs=["A"], reference_wav=str(clip))

    def test_over_long_lines_are_refused_before_any_gpu_work(self):
        with self.assertRaises(ValueError):
            casting.build_plan(lines=["字" * 40], designs=["A"], max_chars=30)

    def test_empty_line_list_is_refused(self):
        with self.assertRaises(ValueError):
            casting.build_plan(lines=["  ", ""], designs=["A"])

    def test_repeats_must_be_positive(self):
        with self.assertRaises(ValueError):
            casting.build_plan(lines=["一"], designs=["A"], repeats=0)

    def test_candidate_ids_accept_short_forms(self):
        self.assertEqual(casting.normalize_candidate_id("7"), "cand_0007")
        self.assertEqual(casting.normalize_candidate_id("cand_7"), "cand_0007")
        self.assertEqual(casting.normalize_candidate_id("CAND_0007"), "cand_0007")
        with self.assertRaises(ValueError):
            casting.normalize_candidate_id("seven")

    def test_line_files_ignore_comments_and_blank_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lines.txt"
            path.write_text("# 注释\n\n  第一句  \n# 又一行注释\n第二句\n", encoding="utf-8")
            self.assertEqual(casting.read_lines(path), ["第一句", "第二句"])


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.batch = self.root / "round1"

    def tearDown(self):
        self._tmp.cleanup()

    def test_appends_are_incremental_and_leave_no_temp_file(self):
        manifest = casting.Manifest.load_or_create(self.batch)
        manifest.append({"candidate_id": "cand_0001", "file": "cand_0001.wav"})
        manifest.append({"candidate_id": "cand_0002", "file": "cand_0002.wav"})
        self.assertEqual(manifest.finished_ids(), {"cand_0001", "cand_0002"})
        self.assertFalse((self.batch / (casting.MANIFEST_NAME + ".tmp")).exists())
        reloaded = casting.Manifest.load_or_create(self.batch)
        self.assertEqual(len(reloaded.entries), 2)

    def test_batch_listing_never_invents_an_empty_batch(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(FileNotFoundError):
                casting.list_batch(self.root / "typo")

    def test_pick_without_a_batch_raises(self):
        with self.assertRaises(FileNotFoundError):
            casting.pick_candidate(self.root / "typo", "1", "sandro_v1")

    def test_dry_run_needs_no_model_and_writes_no_manifest(self):
        plan = casting.build_plan(lines=["一"], designs=["A"])
        with contextlib.redirect_stdout(io.StringIO()):
            written = casting.run_batch(self.batch, plan, dry_run=True)
        self.assertEqual(written, 0)
        self.assertFalse((self.batch / casting.MANIFEST_NAME).exists())

    def test_pick_freezes_the_chosen_candidate_into_a_voice(self):
        self.batch.mkdir(parents=True)
        clip_bytes = b"RIFF____WAVEfmt candidate"
        (self.batch / "cand_0003.wav").write_bytes(clip_bytes)
        (self.batch / casting.MANIFEST_NAME).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": casting.MODEL_ID,
                    "entries": [
                        {
                            "candidate_id": "cand_0003",
                            "file": "cand_0003.wav",
                            "mode": "design",
                            "design": "低沉男声",
                            "line": "把茶喝了。",
                            "cfg_value": 2.0,
                            "inference_timesteps": 10,
                            "seed": 1002,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        previous = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root / "voices")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                asset = casting.pick_candidate(self.batch, "3", "sandro_v1", style="克制")
        finally:
            if previous is None:
                os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
            else:
                os.environ["EXOCORE_TTS_VOICE_ROOT"] = previous

        self.assertEqual(asset.key, "sandro_v1")
        self.assertEqual(asset.display_name, "sandro_v1", "display name falls back to the key")
        self.assertEqual(asset.baseline_instruction, "克制")
        self.assertEqual(asset.prompt_text, "把茶喝了。")
        self.assertEqual(asset.generation_defaults, {"cfg_value": 2.0, "inference_timesteps": 10})
        self.assertEqual(asset.source["candidate_id"], "cand_0003")
        self.assertEqual(asset.source["design"], "低沉男声")
        stored = self.root / "voices" / "sandro_v1" / voices.DEFAULT_REFERENCE_CLIP
        self.assertEqual(stored.read_bytes(), clip_bytes)

    def test_pick_rejects_an_id_that_is_not_in_the_batch(self):
        self.batch.mkdir(parents=True)
        (self.batch / casting.MANIFEST_NAME).write_text(
            json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8"
        )
        with self.assertRaises(KeyError):
            casting.pick_candidate(self.batch, "9", "sandro_v1")


class RegisterTests(unittest.TestCase):
    """Freezing a clip that was produced outside the casting bench (cloud render, hand cut)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root / "voices")

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous
        self._tmp.cleanup()

    def _write_wav(self, name: str = "ref.wav", *, seconds: float = 5.0, peak: float = 0.6) -> Path:
        import numpy as np
        import soundfile as sf

        path = self.root / name
        rate = 24000
        t = np.arange(int(rate * seconds)) / rate
        sf.write(str(path), (peak * np.sin(2 * np.pi * 220 * t)).astype("float32"), rate, format="WAV")
        return path

    def test_register_freezes_the_clip_and_records_its_provenance(self):
        clip = self._write_wav()
        with contextlib.redirect_stdout(io.StringIO()):
            asset = casting.register_reference(
                clip,
                "sandro_v1",
                transcript="把手给我，别躲。",
                display_name="Sandro",
                style="0.8x pace, crisp retroflex",
                origin="gemini-3.8-flash-tts voice_nnvw5qprqmz7",
            )
        self.assertEqual(asset.key, "sandro_v1")
        self.assertEqual(asset.display_name, "Sandro")
        self.assertEqual(asset.prompt_text, "把手给我，别躲。")
        self.assertEqual(asset.baseline_instruction, "0.8x pace, crisp retroflex")
        self.assertEqual(asset.source["origin"], "gemini-3.8-flash-tts voice_nnvw5qprqmz7")
        self.assertAlmostEqual(asset.source["clip_seconds"], 5.0, places=2)
        self.assertEqual(asset.source["sample_rate"], 24000)
        stored = self.root / "voices" / "sandro_v1" / voices.DEFAULT_REFERENCE_CLIP
        self.assertEqual(stored.read_bytes(), clip.read_bytes())
        self.assertEqual(voices.load_voice("sandro_v1").prompt_text, "把手给我，别躲。")

    def test_register_requires_the_exact_transcript(self):
        clip = self._write_wav()
        with self.assertRaises(ValueError) as caught:
            casting.register_reference(clip, "sandro_v1", transcript="   ")
        self.assertIn("transcript", str(caught.exception))

    def test_register_refuses_silent_and_truncated_clips(self):
        silent = self._write_wav("silent.wav", peak=0.0)
        with self.assertRaises(ValueError) as caught:
            casting.register_reference(silent, "sandro_v1", transcript="一")
        self.assertIn("silent", str(caught.exception))

        short = self._write_wav("short.wav", seconds=1.0)
        with self.assertRaises(ValueError) as caught:
            casting.register_reference(short, "sandro_v1", transcript="一")
        self.assertIn("too short", str(caught.exception))

    def test_register_warns_about_a_clipping_reference(self):
        clip = self._write_wav(peak=1.0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            casting.register_reference(clip, "sandro_v1", transcript="一")
        self.assertIn("clipping", out.getvalue())

    def test_register_will_not_silently_replace_a_frozen_voice(self):
        first = self._write_wav("first.wav")
        second = self._write_wav("second.wav", seconds=6.0)
        with contextlib.redirect_stdout(io.StringIO()):
            casting.register_reference(first, "sandro_v1", transcript="一")
        with self.assertRaises(FileExistsError):
            casting.register_reference(second, "sandro_v1", transcript="二")
        with contextlib.redirect_stdout(io.StringIO()):
            casting.register_reference(second, "sandro_v1", transcript="二", force=True)
        self.assertEqual(voices.load_voice("sandro_v1").prompt_text, "二")
        self.assertAlmostEqual(voices.load_voice("sandro_v1").source["clip_seconds"], 6.0, places=2)


class AudioWriteTests(unittest.TestCase):
    def test_wav_is_published_atomically_from_a_tmp_sibling(self):
        """Regression: soundfile cannot infer the container from a `.wav.tmp` name."""
        import soundfile as sf

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cand_0001.wav"
            samples = [0.0, 0.25, -0.25, 0.5]
            casting.write_wav_atomic(target, samples, 48000)
            self.assertTrue(target.is_file())
            self.assertFalse((Path(tmp) / "cand_0001.wav.tmp").exists())
            data, rate = sf.read(str(target))
        self.assertEqual(rate, 48000)
        self.assertEqual(len(data), len(samples))


if __name__ == "__main__":
    unittest.main()
