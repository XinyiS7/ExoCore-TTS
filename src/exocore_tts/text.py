"""Plain-text segmentation for the daemon (Plan/0003 §4.3).

The daemon speaks the text it is handed; the splitter only decides where one local
inference stops and the next one starts. It never rewrites, reorders or drops characters:
the segments, reassembled in order, must reproduce the input (nothing but the whitespace
at a cut seam is allowed to disappear).

`SEGMENT_MAX_CHARS` is a code constant, not an environment knob: it is the measured
single-inference boundary on the RTX 3060 Ti (94 chars peaked at ~6.4 GB reserved of
~6.8 GB usable). The total-request limit is a different, deployable guard and lives in
the service.
"""
from __future__ import annotations

SEGMENT_MAX_CHARS = 120  # one local inference; the 3060 Ti VRAM guard

# Sentence-final punctuation the three voice languages actually use, plus the ellipsis.
_TERMINATORS = "。！？!?…"
# Closing marks that belong to the sentence they follow and must stay on this side of a cut.
_CLOSERS = "\"'”’»)]}>）】」』"


def segment_text(text: str, *, max_chars: int = SEGMENT_MAX_CHARS) -> list[str]:
    """Split `text` into speakable segments of at most `max_chars` characters.

    Preference order: after a sentence-final punctuation mark (and its trailing quotes or
    brackets); otherwise, for an over-long sentence, at the last whitespace that fits;
    and only for a run with no whitespace at all, a hard cut. Every segment is non-empty
    and no character is lost at a cut.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

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
            _emit(segments, text[start:end], max_chars)
            start = end
            index = end
            continue
        index += 1

    _emit(segments, text[start:], max_chars)
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


def _emit(segments: list[str], piece: str, max_chars: int) -> None:
    """Append one cut to `segments`, splitting it further if it is still too long."""
    piece = piece.strip()
    if not piece:
        return
    if len(piece) <= max_chars:
        segments.append(piece)
        return
    rest = piece
    while len(rest) > max_chars:
        # Search one past the limit so a whitespace sitting exactly at the limit is usable.
        window = rest[: max_chars + 1]
        cut = max(window.rfind(" "), window.rfind("\t"), window.rfind("\n"))
        if cut <= 0:
            cut = max_chars  # no whitespace to fall back to: hard cut
        head = rest[:cut].strip()
        if head:
            segments.append(head)
        rest = rest[cut:].lstrip()
    if rest:
        segments.append(rest)
