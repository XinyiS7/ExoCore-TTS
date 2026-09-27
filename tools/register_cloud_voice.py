"""Managed registration for cloud voice assets (Plan/0004 §2.2).

The only sanctioned way to get a provider voice reference into ``voices/<key>/voice.json``.
It never creates a provider voice, never touches the key, and never replaces an existing
manifest without an explicit ``--force``:

    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/register_cloud_voice.py \
        --key sandro_gemini_v1 --kind name --value "Ale 2.5 2" --preflight

``--preflight`` spends exactly one provider render proving that the current key can actually
speak through that reference before anything is registered; it prints sizes and seconds,
never key material.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from exocore_tts import cloud, voices  # noqa: E402

PREFLIGHT_TEXT = "Preflight."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", default="sandro_gemini_v1", help="opaque asset key (directory name)")
    parser.add_argument("--display-name", default="", help="human label; defaults to the key")
    parser.add_argument(
        "--kind",
        required=True,
        choices=voices.CLOUD_VOICE_KINDS,
        help="provider reference kind: an account voice resource id or a designed voice name",
    )
    parser.add_argument("--value", required=True, help="provider voice id or name (never a path)")
    parser.add_argument("--model", default=cloud.MODEL, help="provider model id")
    parser.add_argument("--dotenv", type=Path, default=None, help="env file holding the API key")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="spend one provider render proving the key can speak this reference",
    )
    parser.add_argument("--force", action="store_true", help="replace an existing manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asset = voices.VoiceAsset(
        key=args.key,
        display_name=args.display_name or args.key,
        engine="gemini",
        cloud_voice={"kind": args.kind, "value": args.value},
    )
    try:
        kind, value = voices.cloud_voice_ref(asset)
    except ValueError as exc:
        print(f"FAIL {exc}")
        return 2

    if args.preflight:
        reference = {"voice": value} if kind == "id" else {"prebuilt": value}
        try:
            client = cloud.CloudVoiceClient.from_env(args.dotenv, model=args.model)
            data = client.render(PREFLIGHT_TEXT, style="", **reference)
        except cloud.CloudError as exc:
            print(f"FAIL preflight: {exc}")
            return 1
        print(f"preflight ok: {len(data)} bytes, {cloud.wav_seconds(data):.2f}s")

    try:
        target = voices.save_cloud_voice(asset, force=args.force)
    except FileExistsError as exc:
        print(f"FAIL {exc}")
        return 1
    print(f"registered {asset.key} (kind={kind}) -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
