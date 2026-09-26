"""Segmentation: punctuation rules, the length limit and the no-content-dropped guarantee."""
import unittest

from exocore_tts.text import SEGMENT_MAX_CHARS, segment_text


def without_whitespace(text: str) -> str:
    return "".join(text.split())


class SegmentTextTests(unittest.TestCase):
    def test_short_text_stays_one_segment(self):
        self.assertEqual(segment_text("你好。"), ["你好。"])
        self.assertEqual(segment_text("No punctuation at all"), ["No punctuation at all"])

    def test_chinese_sentences_split_after_trailing_quotes(self):
        self.assertEqual(
            segment_text("他说：“别躲。”然后走了。"),
            ["他说：“别躲。”", "然后走了。"],
        )

    def test_english_and_german_sentence_endings_split(self):
        self.assertEqual(segment_text("Stop! Warum? Los."), ["Stop!", "Warum?", "Los."])

    def test_ellipsis_and_consecutive_punctuation_never_yield_empty_segments(self):
        self.assertEqual(
            segment_text("好……什么？！真的！？"),
            ["好……", "什么？！", "真的！？"],
        )

    def test_decimal_points_and_abbreviation_dots_are_not_sentence_ends(self):
        self.assertEqual(
            segment_text("cfg 3.5 是默认值。z.B. 也行。"),
            ["cfg 3.5 是默认值。", "z.B. 也行。"],
        )
        self.assertEqual(segment_text("z. B. so geht das."), ["z. B. so geht das."])

    def test_quotes_and_brackets_close_the_sentence_they_follow(self):
        self.assertEqual(
            segment_text('He said, "Stop." Then left.'),
            ['He said, "Stop."', "Then left."],
        )
        self.assertEqual(segment_text("（第一句。）第二句。"), ["（第一句。）", "第二句。"])

    def test_no_content_is_dropped_or_reordered(self):
        samples = (
            "他说：“别躲。”然后走了。",
            "Stop! Warum? Los.",
            "好……什么？！真的！？",
            "cfg 3.5 是默认值。z.B. 也行。",
            "第一句。" + "no punctuation " * 8 + "最后一句！",
        )
        for sample in samples:
            segments = segment_text(sample)
            self.assertTrue(segments, sample)
            self.assertEqual(
                without_whitespace("".join(segments)), without_whitespace(sample), sample
            )

    def test_every_segment_respects_the_limit(self):
        sample = "他说：“别躲。”然后走了。" * 30
        for segment in segment_text(sample):
            self.assertLessEqual(len(segment), SEGMENT_MAX_CHARS)

    def test_over_long_unpunctuated_text_is_hard_split(self):
        segments = segment_text("字" * 300)
        self.assertEqual([len(segment) for segment in segments], [120, 120, 60])
        self.assertEqual("".join(segments), "字" * 300)

    def test_over_long_sentence_prefers_whitespace_over_hard_cuts(self):
        text = " ".join(["word"] * 40)
        segments = segment_text(text)
        self.assertGreater(len(segments), 1)
        for segment in segments:
            self.assertLessEqual(len(segment), SEGMENT_MAX_CHARS)
            self.assertEqual(set(segment.split()), {"word"})  # no word was cut in half
        self.assertEqual(without_whitespace("".join(segments)), without_whitespace(text))

    def test_random_texts_never_lose_content_or_exceed_the_limit(self):
        import random

        rng = random.Random(20260927)
        alphabet = "中文测试句子。！？…，、" + "abc XYZ.?!\"'" + "0123456789 \n\t" + "（）“”‘’"
        for _ in range(200):
            sample = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 400)))
            if not sample.strip():
                continue
            segments = segment_text(sample)
            self.assertTrue(segments, sample)
            for segment in segments:
                self.assertTrue(segment)
                self.assertLessEqual(len(segment), SEGMENT_MAX_CHARS, sample)
            # order and content survive every split decision
            self.assertEqual(
                without_whitespace("".join(segments)), without_whitespace(sample), sample
            )

    def test_limit_is_configurable_and_validated(self):
        self.assertEqual(segment_text("一二三四五。", max_chars=3), ["一二三", "四五。"])
        with self.assertRaises(ValueError):
            segment_text("一二三", max_chars=0)

    def test_whitespace_only_text_yields_no_segments(self):
        self.assertEqual(segment_text("   \n\t "), [])


if __name__ == "__main__":
    unittest.main()
