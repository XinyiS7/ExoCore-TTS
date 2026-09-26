"""Spot check logic: normalisation, similarity, verdict rules. No network, no GPU."""
import json
import tempfile
import unittest
from pathlib import Path

from exocore_tts import verify


class FakeClient:
    """Stands in for google-genai: returns queued transcripts, records what it was asked."""

    def __init__(self, transcripts):
        self.transcripts = list(transcripts)
        self.prompts = []
        self.models = self._Models(self)

    class _Models:
        def __init__(self, parent):
            self.parent = parent

        def generate_content(self, *, model, contents):
            self.parent.models_seen = model
            self.parent.prompts.append(contents[-1].text)

            class _Response:
                text = self.parent.transcripts.pop(0)

            return _Response()


class NormaliseTests(unittest.TestCase):
    def test_chinese_keeps_only_characters(self):
        self.assertEqual(verify.normalise("把手给我，别躲。", "zh"), "把手给我别躲")

    def test_latin_languages_keep_words_and_fold_case(self):
        self.assertEqual(verify.normalise("Komm her, du musst nichts erklären.", "de"),
                         "komm her du musst nichts erklären")
        self.assertEqual(verify.normalise("You are mine — not even you.", "en"),
                         "you are mine not even you")

    def test_similarity_is_exact_for_a_clean_transcript(self):
        self.assertEqual(verify.similarity("把手给我，别躲。", "把手给我别躲", "zh"), 1.0)

    def test_similarity_drops_when_a_word_is_lost(self):
        self.assertLess(verify.similarity("把手给我，别躲。", "把手给我", "zh"), verify.OK_THRESHOLD)

    def test_empty_transcript_is_zero_not_an_error(self):
        self.assertEqual(verify.similarity("别躲。", "   ", "zh"), 0.0)

    def test_uncomparable_intended_line_is_refused(self):
        with self.assertRaises(ValueError):
            verify.similarity("!!!", "什么", "zh")


class VerdictTests(unittest.TestCase):
    def test_every_pass_matching_is_clean(self):
        result = verify.CheckResult(candidate="cand_0001", line="别躲。", transcripts=["别躲", "别躲"],
                                    ratios=[1.0, 1.0])
        self.assertEqual(result.verdict, "ok")

    def test_one_clean_pass_is_enough(self):
        """A pass that heard the line exactly proves the content is there; another pass
        mishearing intonation must not turn a correct render into a flag."""
        result = verify.CheckResult(candidate="cand_0001", line="把手给我，别躲。",
                                    transcripts=["把手给我，别躲", "给我别躲"],
                                    ratios=[1.0, 0.8])
        self.assertEqual(result.verdict, "ok")

    def test_intonation_level_near_miss_asks_for_an_ear(self):
        result = verify.CheckResult(candidate="cand_0001", line="把手给我，别躲。",
                                    transcripts=["把手给我，别多。", "把手给我，别多"],
                                    ratios=[0.93, 0.93])
        self.assertEqual(result.verdict, "listen")

    def test_garbled_output_is_suspect_not_a_near_miss(self):
        result = verify.CheckResult(candidate="cand_0003", line="别躲。", transcripts=["别的", "别的"],
                                    ratios=[0.33, 0.33])
        self.assertEqual(result.verdict, "suspect")

    def test_no_transcript_is_an_error(self):
        self.assertEqual(verify.CheckResult(candidate="cand_0004", line="一").verdict, "error")


class CheckClipsTests(unittest.TestCase):
    def _clip(self, tmp: Path) -> Path:
        clip = tmp / "cand_0001.wav"
        clip.write_bytes(b"RIFF____WAVEfmt ")  # the transcriber is faked, so bytes are enough
        return clip

    def test_two_passes_are_taken_per_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = self._clip(Path(tmp))
            client = FakeClient(["把手给我，别躲", "把手给我，别躲"])
            results = verify.check_clips([("cand_0001", clip, "把手给我，别躲。")], language="zh", client=client)
        self.assertEqual(len(results[0].transcripts), 2)
        self.assertEqual(results[0].verdict, "ok")
        self.assertEqual(client.models_seen, verify.DEFAULT_TRANSCRIBE_MODEL)

    def test_the_prompt_follows_the_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = self._clip(Path(tmp))
            client = FakeClient(["Komm her.", "Komm her."])
            verify.check_clips([("cand_0001", clip, "Komm her.")], language="de", passes=2, client=client)
        self.assertIn("Transkribiere", client.prompts[0])

    def test_single_pass_mode_still_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            clip = self._clip(Path(tmp))
            client = FakeClient(["Komm her."])
            results = verify.check_clips([("cand_0001", clip, "Komm her.")], language="de", passes=1,
                                         client=client)
        self.assertEqual(results[0].verdict, "ok")


class BatchTests(unittest.TestCase):
    def test_batch_entries_become_check_triples(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            (batch / "cand_0001.wav").write_bytes(b"RIFF____WAVEfmt ")
            (batch / verify.MANIFEST_NAME).write_text(
                json.dumps({"entries": [{"candidate_id": "cand_0001", "file": "cand_0001.wav",
                                         "line": "别躲。"}]}),
                encoding="utf-8",
            )
            clips = verify.clips_from_batch(batch)
        self.assertEqual(clips, [("cand_0001", batch / "cand_0001.wav", "别躲。")])

    def test_missing_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                verify.clips_from_batch(Path(tmp))


class KeyTests(unittest.TestCase):
    def test_missing_env_file_is_reported_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                verify.read_api_key(Path(tmp) / "nope.env")

    def test_key_is_read_and_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text('OTHER=1\nGEMINI_API_KEY="abc123"\n', encoding="utf-8")
            self.assertEqual(verify.read_api_key(env), "abc123")

    def test_missing_key_names_the_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("OTHER=1\n", encoding="utf-8")
            with self.assertRaises(KeyError):
                verify.read_api_key(env)


if __name__ == "__main__":
    unittest.main()
