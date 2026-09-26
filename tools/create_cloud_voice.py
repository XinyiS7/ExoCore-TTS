"""Create (or reuse) a cloud voice resource from a style prompt.

A prompted voice is what turns a written delivery description into a reusable asset: once it
exists, `tools/render_reference.py` can render it in any language, and the local engine can
clone the result. The id it writes is account-level state, so the record file under
`tools/cloud/voices/` is the thing to keep in git.

    python tools/create_cloud_voice.py            # reuse the recorded voice if it exists
    python tools/create_cloud_voice.py --force    # create a fresh one and rewrite the record

Run it with the service environment (`voxcpm_runtime`).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exocore_tts import cloud  # noqa: E402

DEFAULT_PROMPT = Path("tools/cloud/prompts/ale.txt")
DEFAULT_RECORD = Path("tools/cloud/voices/ale.json")


def load_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_record(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    parser.add_argument("--display-name", default="", help="defaults to the record's display_name or 'Ale'")
    parser.add_argument("--model", default=cloud.MODEL)
    parser.add_argument("--force", action="store_true", help="create a new voice even if one is recorded")
    parser.add_argument("--dotenv", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise SystemExit(f"{args.prompt_file} is empty")
    record = load_record(args.record)
    display_name = args.display_name or record.get("display_name") or "Ale"

    if record.get("voice_id") and not args.force:
        print(f"reusing recorded voice: {record['voice_id']}  (display_name={display_name})")
        print(f"record -> {args.record}")
        return 0

    client = cloud.CloudVoiceClient.from_env(args.dotenv, model=args.model)
    voice_id = client.create_voice(display_name, prompt)
    write_record(args.record, {
        "display_name": display_name,
        "model": args.model,
        "voice_id": voice_id,
        "prompt": args.prompt_file.name,
        "created": date.today().isoformat(),
        "note": "Account-level prompted voice. Recreate with this prompt if it expires; the id itself lives here because the API key does not.",
    })
    print(f"created voice: {voice_id}  (display_name={display_name})")
    print(f"record -> {args.record}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
