#!/usr/bin/env python.exe
"""Spot check a rendered batch: transcribe it back and compare with the intended lines.

    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/verify_audio.py \
        --batch candidates/final_test --language zh

Needs a cloud API key (read from the sibling ExoCore `.env` by default; override with
EXOCORE_TTS_DOTENV). Costs a few seconds of audio per pass, no GPU.
"""
import sys

try:
    from exocore_tts import verify
except ImportError as exc:  # wrong interpreter, or the package was never installed
    sys.exit(f"Cannot import exocore_tts ({exc}). See README (environment section).")

if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="verify_audio.py", description=verify.__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--batch", type=Path, help="casting batch directory (manifest supplies the lines)")
    source.add_argument("--clip", type=Path, help="one audio file, needs --text")
    parser.add_argument("--text", default="", help="the line --clip was asked to speak")
    parser.add_argument("--language", default="zh", choices=sorted(verify.TRANSCRIBE_PROMPTS))
    parser.add_argument("--passes", type=int, default=2, help="transcription passes; a single pass is not trusted")
    parser.add_argument("--model", default=verify.DEFAULT_TRANSCRIBE_MODEL)
    args = parser.parse_args()

    if args.clip:
        if not args.text.strip():
            sys.exit("--clip needs --text (the line it was asked to speak)")
        clips = [(args.clip.stem, args.clip, args.text)]
    else:
        clips = verify.clips_from_batch(args.batch)
    if not clips:
        sys.exit("nothing to check")

    results = verify.check_clips(clips, language=args.language, passes=args.passes, model=args.model)
    print(verify.report(results))
    raise SystemExit(1 if any(result.verdict == "suspect" for result in results) else 0)
