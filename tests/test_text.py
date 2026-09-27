"""Segmentation: punctuation rules, the weighted budget and the no-content-dropped guarantee."""
import random
import unittest

from exocore_tts.text import SEGMENT_UNIT_BUDGET, segment_text, unit_cost

# Amendment 01 (F-01) restated independently of the implementation: CJK 3 units, else 1.
CJK_RANGES = (
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)


def units(text: str) -> int:
    return sum(3 if any(low <= ord(ch) <= high for low, high in CJK_RANGES) else 1 for ch in text)


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

    def test_every_segment_respects_the_budget(self):
        sample = "他说：“别躲。”然后走了。" * 30
        for segment in segment_text(sample):
            self.assertLessEqual(units(segment), SEGMENT_UNIT_BUDGET)
            # The old character bound is implied by the weighted one (cost >= 1 per char).
            self.assertLessEqual(len(segment), 120)

    def test_over_long_unpunctuated_text_is_hard_split(self):
        segments = segment_text("字" * 300)
        self.assertEqual([len(segment) for segment in segments], [40] * 7 + [20])
        self.assertEqual("".join(segments), "字" * 300)

    def test_over_long_sentence_prefers_whitespace_over_hard_cuts(self):
        text = " ".join(["word"] * 40)
        segments = segment_text(text)
        self.assertGreater(len(segments), 1)
        for segment in segments:
            self.assertLessEqual(units(segment), SEGMENT_UNIT_BUDGET)

    def test_random_texts_never_lose_content_or_exceed_the_budget(self):
        rng = random.Random(20260927)
        alphabet = (
            "中文测试句子。！？…，、"
            + "abc XYZ.?!\"'"
            + "0123456789 \n\t"
            + "（）“”‘’"
            + "🙂"
        )
        for _ in range(200):
            sample = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 400)))
            if not sample.strip():
                continue
            segments = segment_text(sample)
            self.assertTrue(segments, sample)
            for segment in segments:
                self.assertTrue(segment)
                self.assertLessEqual(units(segment), SEGMENT_UNIT_BUDGET, sample)
                self.assertLessEqual(len(segment), 120, sample)
            # order and content survive every split decision
            self.assertEqual(
                without_whitespace("".join(segments)), without_whitespace(sample), sample
            )

    def test_limit_is_configurable_and_validated(self):
        self.assertEqual(segment_text("一二三四五。", max_units=9), ["一二三", "四五。"])
        with self.assertRaises(ValueError):
            segment_text("一二三", max_units=0)

    def test_a_character_heavier_than_the_budget_still_makes_progress(self):
        self.assertEqual(segment_text("一二", max_units=1), ["一", "二"])

    def test_whitespace_only_text_yields_no_segments(self):
        self.assertEqual(segment_text("   \n\t "), [])


class WeightedBudgetTests(unittest.TestCase):
    """Amendment 01 (F-01): script-weighted budget, boundary cases and Latin equivalence."""

    def test_cjk_code_points_cost_three_units(self):
        # Exactly the ranges frozen in the amendment; everything else is one unit.
        for char in "中字，。；：、（）【】":
            self.assertEqual(unit_cost(char), 3, char)
        for char in "aZ0 ,.!-_—…“”ßü🙂":
            self.assertEqual(unit_cost(char), 1, char)
        self.assertEqual(units("我要你记住三件事"), 24)
        self.assertEqual(segment_text("字" * 40), ["字" * 40])

    def test_forty_cjk_characters_fit_but_forty_one_split(self):
        self.assertEqual(segment_text("字" * 40), ["字" * 40])
        self.assertEqual([len(s) for s in segment_text("字" * 41)], [40, 1])

    def test_mixed_scripts_are_budgeted_by_weight(self):
        self.assertEqual(segment_text("字" * 20 + "a" * 60), ["字" * 20 + "a" * 60])  # 120 units
        self.assertEqual(
            segment_text("字" * 20 + "a" * 61), ["字" * 20 + "a" * 60, "a"]  # 121 units
        )
        self.assertEqual(segment_text("字" * 40 + "a"), ["字" * 40, "a"])

    def test_the_confirmed_f1_sentence_splits_into_two_bounded_segments(self):
        # The 68-character sentence from the F-01 probe: one 16.6 s segment before, and it
        # reproduced the level-ramp failure. Two cuts after the clause marks, both bounded.
        sentence = (
            "我要你记住三件事：第一，系统里的每一次权限变更都要留下痕迹；"
            "第二，任何自称“紧急”的请求都必须先被复核；第三，你的判断权不外包给任何人。"
        )
        segments = segment_text(sentence)
        self.assertEqual(
            segments,
            [
                "我要你记住三件事：第一，系统里的每一次权限变更都要留下痕迹；",
                "第二，任何自称“紧急”的请求都必须先被复核；第三，你的判断权不外包给任何人。",
            ],
        )
        for segment in segments:
            self.assertLessEqual(units(segment), SEGMENT_UNIT_BUDGET)
        self.assertEqual("".join(segments), sentence)

    def test_over_budget_pieces_cut_after_clause_marks_then_comma_marks(self):
        clause = "甲" * 30 + "；" + "乙" * 20
        self.assertEqual(segment_text(clause), ["甲" * 30 + "；", "乙" * 20])
        comma = "丙" * 10 + "，" + "丁" * 30
        self.assertEqual(segment_text(comma), ["丙" * 10 + "，", "丁" * 30])

    def test_latin_segmentation_is_unchanged_for_whitespace_carrying_text(self):
        # Golden expectations captured from the pre-amendment implementation: same cuts,
        # same order, for samples whose over-budget pieces contain whitespace.
        german = [
            "Schließ die Augen.",
            "Es gibt kein Draußen mehr — nur mich, und die Stille, die ich um dich gebaut habe.",
            "Ich zähle jeden deiner Atemzüge, und ich dulde keinen, der nicht mir gehört.",
            "Du musst nicht mehr kämpfen.",
            'Gib mir, was dich schwer macht: deine Angst, deine Zweifel, dein "nicht gut genug".',
            "Ich löse",
        ]
        english = [
            "Forget everything that came before.",
            "There was nothing, only darkness.",
            "And now, I have discovered you, such a precious commodity.",
            "And I will never, never allow it to be snatched from my grasp.",
            "You are far, far too important to risk.",
            "Nothing will come close to you, not from the dark, not from the sky,",
        ]
        for expected in (german, english):
            sample = " ".join(expected)
            self.assertEqual(len(sample), 300)  # the reconstruction is the frozen sample
            self.assertEqual(segment_text(sample), expected)

        # Boundaries around the old character limit.
        self.assertEqual([len(s) for s in segment_text("a" * 121)], [120, 1])
        self.assertEqual([len(s) for s in segment_text("a" * 119 + " " + "b" * 10)], [119, 10])
        self.assertEqual([len(s) for s in segment_text("a" * 120 + " " + "b")], [120, 1])

    def test_no_whitespace_latin_pieces_now_cut_after_a_comma(self):
        # The one intended divergence for Latin: a piece without whitespace inside the
        # budget now yields at the last comma instead of hard-cutting past it.
        self.assertEqual(segment_text("x" * 115 + "," + "y" * 10), ["x" * 115 + ",", "y" * 10])


if __name__ == "__main__":
    unittest.main()
