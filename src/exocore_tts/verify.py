"""Spot check: did the engine actually say what it was asked to say?

A rendered line is transcribed by a cloud model and compared against the line that was supposed
to be spoken. This is an **alarm, not a verdict**: a transcriber hears intonation as content
(one perfectly correct candidate was reported as missing its last word), so a single pass is
never trusted. Two passes must agree with each other *and* disagree with the intended text
before a candidate is called suspect -- and whoever listens to the audio still has the final
word.

Why it exists at all: this engine misreads polyphones at random (还 hái rendered as huán) and
occasionally drops a syllable. Both are re-rollable, but only if something notices.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from exocore_tts import config
from exocore_tts.casting import MANIFEST_NAME

DEFAULT_TRANSCRIBE_MODEL = "gemini-2.5-flash"
OK_THRESHOLD = 0.999
SUSPECT_THRESHOLD = 0.8

TRANSCRIBE_PROMPTS = {
    "zh": "把这段普通话音频完整转写成汉字，保留标点，不要漏字，不要改写，不要解释。",
    "en": "Transcribe this audio verbatim in its original language. Keep punctuation, "
          "do not translate, do not explain.",
    "de": "Transkribiere diese Audiodatei wörtlich in ihrer Originalsprache. Behalte die "
          "Satzzeichen, übersetze nicht, erkläre nichts.",
}
CHARACTER_LANGUAGES = {"zh"}


@dataclass
class CheckResult:
    """Verdict for one candidate."""

    candidate: str
    line: str
    language: str = "zh"
    transcripts: list[str] = field(default_factory=list)
    ratios: list[float] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Advisory severity, never a judgement on the audio.

        - `ok`: at least one pass heard the intended line exactly. One clean reading is
          enough; the other pass mishearing intonation does not make the render wrong.
        - `listen`: no exact match but the content largely survived -- heard as stress and
          intonation, so the ear decides.
        - `suspect`: the content itself did not survive (dropped words, garbled syllables,
          or a syllable carrying the wrong vowel sound).
        """
        if not self.transcripts:
            return "error"
        if any(ratio >= OK_THRESHOLD for ratio in self.ratios):
            return "ok"
        if max(self.ratios) < SUSPECT_THRESHOLD:
            return "suspect"
        return "listen"


def normalise(text: str, language: str) -> str:
    """Reduce a transcript and an intended line to the same comparable shape."""
    if language in CHARACTER_LANGUAGES:
        return "".join(re.findall(r"[\u4e00-\u9fff]", text))
    words = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)
    return " ".join(words)


def similarity(intended: str, heard: str, language: str) -> float:
    """How much of the intended line survived transcription (0.0 - 1.0)."""
    want, got = normalise(intended, language), normalise(heard, language)
    if not want:
        raise ValueError(f"Nothing comparable in the intended line: {intended!r}")
    if not got:
        return 0.0
    return difflib.SequenceMatcher(None, want, got).ratio()


def read_api_key(dotenv: Path | None = None) -> str:
    """Read GEMINI_API_KEY from the ExoCore env file. Never logs or returns it anywhere else."""
    path = Path(dotenv) if dotenv else config.dotenv_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"No env file at {path}. Point EXOCORE_TTS_DOTENV at the file holding GEMINI_API_KEY."
        )
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise KeyError(f"GEMINI_API_KEY is not set in {path}")


def transcribe(client, clip: Path, language: str, model: str = DEFAULT_TRANSCRIBE_MODEL) -> str:
    """One transcription pass. `client` is a google-genai Client (injected for testing)."""
    from google.genai import types

    prompt = TRANSCRIBE_PROMPTS.get(language, TRANSCRIBE_PROMPTS["en"])
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=Path(clip).read_bytes(), mime_type="audio/wav"),
            types.Part(text=prompt),
        ],
    )
    return (response.text or "").strip()


def check_clips(
    clips: list[tuple[str, Path, str]],
    *,
    language: str,
    client=None,
    passes: int = 2,
    model: str = DEFAULT_TRANSCRIBE_MODEL,
) -> list[CheckResult]:
    """Check `(name, clip, intended_line)` triples. `client=None` loads a real one lazily."""
    if client is None:
        from google import genai

        client = genai.Client(api_key=read_api_key())
    results = []
    for name, clip, line in clips:
        result = CheckResult(candidate=name, line=line, language=language)
        for _ in range(passes):
            heard = transcribe(client, clip, language, model=model)
            result.transcripts.append(heard)
            result.ratios.append(similarity(line, heard, language))
        results.append(result)
    return results


def clips_from_batch(batch_dir: Path) -> list[tuple[str, Path, str]]:
    """Every finished candidate of a casting batch, with the line it was asked to speak."""
    batch_dir = Path(batch_dir)
    manifest_path = batch_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Not a finished batch (no {MANIFEST_NAME}): {batch_dir}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [
        (entry["candidate_id"], batch_dir / entry["file"], entry.get("line", ""))
        for entry in payload.get("entries", [])
    ]


def report(results: list[CheckResult]) -> str:
    """Human-readable verdict table. Suspect lines print what was heard, for the ear to judge."""
    lines = []
    for result in results:
        lines.append(f"{result.verdict.upper():<8}{result.candidate}  best {max(result.ratios or [0]):5.1%}")
        if result.verdict != "ok":
            lines.append(f"        intended: {normalise(result.line, result.language) or result.line}")
            for transcript in result.transcripts:
                lines.append(f"        heard   : {transcript}")
    suspects = [r for r in results if r.verdict == "suspect"]
    flagged = [r for r in results if r.verdict != "ok"]
    lines.append(
        f"\n{len(results) - len(flagged)}/{len(results)} heard clean; "
        f"{len(flagged)} worth an ear ({len(suspects)} of them clearly off)."
    )
    return "\n".join(lines)
