"""Render a reference clip through a cloud voice resource.

This is how the frozen references in `voices/` were made: the cloud voice sets timbre,
accent and pace; the local engine clones it afterwards. The output WAV is what gets handed
to `tools/cast.py register` together with the exact transcript.

    python tools/render_reference.py \
        --record tools/cloud/voices/ale.json \
        --text-file candidates/pace_probe/transcript_zh_v8.txt \
        --style-file tools/cloud/prompts/recipe_zh.txt \
        --out candidates/zh_v8_v2 --label v8_v2 --takes 2

Run it with the service environment (`voxcpm_runtime`): it holds google-genai plus the
audio stack. The key is read from `GEMINI_API_KEY` or the sibling ExoCore `.env`, and is
never printed.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exocore_tts import cloud  # noqa: E402


def wav_seconds(data: bytes) -> float:
    """Duration of a PCM WAV byte string; 0.0 when the header is unreadable."""
    return cloud.wav_seconds(data)


def voice_id_from(record: Path) -> str:
    payload = json.loads(Path(record).read_text(encoding="utf-8"))
    voice_id = str(payload.get("voice_id", "")).strip()
    if not voice_id:
        raise SystemExit(f"{record} has no voice_id")
    return voice_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--text-file", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--label", required=True, help="output basename, e.g. v8_v2")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--record", type=Path, help="cloud voice record JSON (uses its voice_id)")
    source.add_argument("--voice-file", type=Path, help="file holding just the voice id")
    source.add_argument("--voice", default="", help="voice resource id")
    source.add_argument("--prebuilt", default="", help="prebuilt voice name instead of a resource")
    style = parser.add_mutually_exclusive_group()
    style.add_argument("--style", default="", help="style instruction for this render")
    style.add_argument("--style-file", type=Path, help="file holding the style instruction")
    parser.add_argument("--takes", type=int, default=1)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--model", default=cloud.MODEL)
    parser.add_argument("--dotenv", type=Path, default=None, help="env file holding the API key")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = args.text_file.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"{args.text_file} is empty")
    style = args.style or (args.style_file.read_text(encoding="utf-8").strip() if args.style_file else "")
    voice = args.voice
    if args.record:
        voice = voice_id_from(args.record)
    elif args.voice_file:
        voice = args.voice_file.read_text(encoding="utf-8").strip()
    if args.takes < 1:
        raise SystemExit("--takes must be >= 1")

    client = cloud.CloudVoiceClient.from_env(args.dotenv, model=args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    for take in range(1, args.takes + 1):
        try:
            data = client.render(text, voice=voice, prebuilt=args.prebuilt, style=style,
                                 temperature=args.temperature)
        except cloud.CloudError as exc:
            print(f"FAIL {args.label}_{take}: {exc}")
            continue
        target = args.out / f"{args.label}_{take}.wav"
        target.write_bytes(data)
        print(f"  {target.name:<22} {wav_seconds(data):5.2f}s  (transcript = {args.text_file.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
