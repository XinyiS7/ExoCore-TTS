"""Plain-text segmentation for the daemon (Plan/0003 §4.3, Amendment 01).

Two invariants live behind the single budget constant below:

* the historical VRAM outer bound -- one local inference must stay inside the measured
  3060 Ti envelope (94 chars peaked at ~6.4 GB reserved of ~6.8 GB usable);
* the quality bound added by Amendment 01 (F-01) -- a segment must not stay on the engine
  long enough to reproduce the confirmed level-ramp/clipping failure. Chinese packs roughly
  three times the audio per character, so the budget is script-weighted: CJK code points
  cost 3 units, everything else 1. `SEGMENT_UNIT_BUDGET = 120` therefore admits at most 40
  CJK characters, keeps pure Latin at the old 120 characters, and implies the character
  bound for every input (cost is never below one unit per character).

The splitter only decides where one local inference stops and the next one starts. It never
rewrites, reorders or drops characters: the segments, reassembled in order, must reproduce
the input (nothing but the whitespace at a cut seam is allowed to disappear).
"""
from __future__ import annotations

SEGMENT_UNIT_BUDGET = 120  # weighted units per local inference (Amendment 01 / F-01)

# The code points that cost three units: CJK punctuation, ideographs and fullwidth forms.
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3400, 0x4DBF),  # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF00, 0xFFEF),  # halfwidth and fullwidth forms
)

# Sentence-final punctuation the three voice languages actually use, plus the ellipsis.
_TERMINATORS = "。！？!?…"
# Closing marks that belong to the sentence they follow and must stay on this side of a cut.
_CLOSERS = "\"'”’»)]}>）】」』"
# Secondary cut points for an over-budget piece without whitespace: cut *after* the mark.
_CLAUSE_MARKS = "；;：:"
_COMMA_MARKS = "，,、"


def unit_cost(char: str) -> int:
    """Return the budget cost of one character: CJK 3 units, everything else 1."""
    point = ord(char)
    return 3 if any(low <= point <= high for low, high in _CJK_RANGES) else 1


def segment_units(text: str) -> int:
    """Return the weighted budget cost of `text` (CJK 3 units per character, else 1)."""
    return sum(unit_cost(char) for char in text)


def segment_text(text: str, *, max_units: int = SEGMENT_UNIT_BUDGET) -> list[str]:
    """Split `text` into speakable segments of at most `max_units` weighted units.

    Preference order: after a sentence-final punctuation mark (and its trailing quotes or
    brackets); otherwise, for an over-budget piece, at the last whitespace inside the
    budget, then after the last clause mark (；：), then after the last comma-level mark
    (，、), and only for a run with no separator at all, a hard cut. Every segment is
    non-empty and no character is lost at a cut.
    """
    if max_units < 1:
        raise ValueError("max_units must be >= 1")

    segments: list[str] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in _TERMINATORS or (char == "." and _is_sentence_period(text, index)):
            end = index + 1
            # "...", "?!" or "！！！" are one ending, never several empty ones.
            while end < length and (text[end] in _TERMINATORS or text[end] == "."):
                end += 1
            while end < length and text[end] in _CLOSERS:
                end += 1
            _emit(segments, text[start:end], max_units)
            start = end
            index = end
            continue
        index += 1

    _emit(segments, text[start:], max_units)
    return segments


def _is_sentence_period(text: str, index: int) -> bool:
    """Tell a sentence-ending "." from a decimal point or an abbreviation dot."""
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous.isdigit() and following.isdigit():
        return False  # 3.5
    previous_letter = previous.isascii() and previous.isalpha()
    if previous_letter and following.isascii() and following.isalpha():
        return False  # z.B. / U.S.A.
    if previous_letter:
        before = text[index - 2] if index >= 2 else ""
        if not (before.isascii() and before.isalpha()):
            return False  # z. B. / a lone initial: "X." is an abbreviation, not a sentence end
    return True


def _emit(segments: list[str], piece: str, max_units: int) -> None:
    """Append one cut to `segments`, splitting it further while it is still over budget."""
    piece = piece.strip()
    if not piece:
        return
    if segment_units(piece) <= max_units:
        segments.append(piece)
        return
    rest = piece
    while segment_units(rest) > max_units:
        limit = _budget_prefix(rest, max_units)
        if limit < 1:
            # A single code point heavier than the whole budget is its own cut: the only
            # way to make progress without dropping it.
            limit = 1
        window = rest[:limit]
        cut = max(window.rfind(" "), window.rfind("\t"), window.rfind("\n"))
        if cut <= 0:
            cut = _mark_cut(window)
        if cut <= 0:
            cut = limit  # no separator to fall back to: hard cut
        head = rest[:cut].strip()
        if head:
            segments.append(head)
        rest = rest[cut:].lstrip()
    if rest:
        segments.append(rest)


def _budget_prefix(text: str, max_units: int) -> int:
    """Return the index just past the longest prefix whose cost stays within budget."""
    used = 0
    for index, char in enumerate(text):
        used += unit_cost(char)
        if used > max_units:
            return index
    return len(text)


def _mark_cut(window: str) -> int:
    """Return the position just after the last clause mark, else comma mark, else 0."""
    for marks in (_CLAUSE_MARKS, _COMMA_MARKS):
        position = max((window.rfind(mark) for mark in marks), default=-1)
        if position >= 1:  # never emit a punctuation-only head
            return position + 1
    return 0
