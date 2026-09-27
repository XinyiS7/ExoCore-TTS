#!/usr/bin/env python.exe
"""Delivery capability probe for the voxcpm2 backend (Plan/0003 §3 / §8 step 5).

Answers one question with audio: can a natural-language `delivery` direction change how a
frozen voice performs a line *without* being spoken aloud and without damaging the voice?

The engine exposes no performance control at all: `VoxCPM._generate` accepts text /
prompt_wav_path / prompt_text / reference_wav_path / cfg_value / inference_timesteps /
min_len / max_len / normalize / denoise / retry_badcase. So there are exactly two places a
direction could travel, and this tool probes both:

    --mode text_prefix   the direction is prefixed to the spoken line
                         (measured 2026-09-27: spoken aloud in all four forms -> unusable)
    --mode prompt_text   the direction is prefixed to the clone prompt's transcript
                         (under investigation: not spoken, but is the change directed?)

Every draw records its exact call in `probe.json`. Same seed + same call is bit-identical
(verified), so a difference between two draws of one line is caused by the input, not noise.

    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/probe_delivery.py \
        --mode prompt_text --voice sandro_v1 --line "现在，去吃饭。" \
        --variant "soft=(亲密、柔和、贴着耳朵说)" --variant "dominant=(强势、命令)" \
        --out candidates/delivery_probe/round2_short

Then judge by ear, and cross-check pollution objectively:

    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/verify_audio.py \
        --clip <file>.wav --text "<the line, without any instruction>" --language zh
"""
import sys

try:
    from exocore_tts import voxcpm, voices
except ImportError as exc:  # wrong interpreter, or the package was never installed
    sys.exit(f"Cannot import exocore_tts ({exc}). See README (environment section).")

MODE_TEXT_PREFIX = "text_prefix"
MODE_PROMPT_TEXT = "prompt_text"
MODES = (MODE_TEXT_PREFIX, MODE_PROMPT_TEXT)

# Default candidate forms for `--mode text_prefix`: instruction shapes a backend mapping
# could realistically use. The label is a file name; the value is the exact text prefix.
DEFAULT_TEXT_PREFIX_VARIANTS: tuple[tuple[str, str], ...] = (
    ("paren_zh_direction", "(放慢一点，克制一些)"),
    ("paren_zh_imperative", "(低声、缓慢地说)"),
    ("paren_zh_fullwidth", "（放慢一点，克制一些）"),
    ("paren_en_tag", "(slower, restrained, quieter)"),
)

CONTROL_SEEDS = (2001, 2002)
VARIANT_SEED = 2001


def parse_variant(raw: str) -> tuple[str, str]:
    """`label=instruction text` -> (label, instruction)."""
    label, separator, instruction = raw.partition("=")
    if not separator or not label.strip() or not instruction.strip():
        raise ValueError(f"--variant wants 'label=instruction text', got {raw!r}")
    return label.strip(), instruction.strip()


def main(argv=None) -> int:
    import argparse
    import json
    from pathlib import Path

    import soundfile as sf

    parser = argparse.ArgumentParser(prog="probe_delivery.py", description=__doc__.splitlines()[0])
    parser.add_argument("--voice", default="sandro_v1", help="frozen voice key to probe")
    parser.add_argument("--line", required=True, help="the line every draw speaks")
    parser.add_argument("--out", type=Path, required=True, help="output directory for the probe")
    parser.add_argument("--mode", choices=MODES, default=MODE_TEXT_PREFIX)
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        metavar="LABEL=INSTRUCTION",
        help="candidate direction; repeatable. Defaults to the text-prefix forms.",
    )
    parser.add_argument("--device", default=None, help="torch device, e.g. cuda:0 (default: auto)")
    args = parser.parse_args(argv)

    variants = [parse_variant(raw) for raw in args.variant]
    if not variants:
        if args.mode != MODE_TEXT_PREFIX:
            parser.error(f"--mode {args.mode} needs at least one --variant LABEL=INSTRUCTION")
        variants = list(DEFAULT_TEXT_PREFIX_VARIANTS)

    asset = voices.load_voice(args.voice)
    reference = voices.voice_dir(asset.key) / (
        asset.reference_clip or voices.DEFAULT_REFERENCE_CLIP
    )
    if not reference.is_file():
        sys.exit(f"Voice {asset.key!r} has no reference clip at {reference}")

    defaults = asset.generation_defaults or {}
    cfg_value = float(defaults.get("cfg_value", voxcpm.DEFAULT_CFG))
    inference_timesteps = int(defaults.get("inference_timesteps", voxcpm.DEFAULT_TIMESTEPS))

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"voice   : {asset.key}  ({asset.display_name or asset.key})")
    print(f"mode    : {args.mode}")
    print(f"line    : {args.line!r}")
    print(
        f"recipe  : cfg={cfg_value} timesteps={inference_timesteps} "
        f"prompt_text={asset.prompt_text[:24]!r}"
    )
    print("loading model (the slow part, once) ...", flush=True)
    loaded = voxcpm.load_model(device=args.device)
    print(f"model   : sample_rate={loaded.sample_rate}")

    draws: list[dict] = [
        {"name": "control_a", "instruction": "", "seed": CONTROL_SEEDS[0]},
        {"name": "control_b", "instruction": "", "seed": CONTROL_SEEDS[1]},
    ]
    for name, instruction in variants:
        draws.append({"name": name, "instruction": instruction, "seed": VARIANT_SEED})

    records = []
    for draw in draws:
        instruction = draw["instruction"]
        if args.mode == MODE_TEXT_PREFIX:
            model_text = instruction + args.line
            prompt_text = asset.prompt_text
        else:
            model_text = args.line
            prompt_text = instruction + asset.prompt_text
        samples, facts = voxcpm.generate(
            loaded,
            text=model_text,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            seed=draw["seed"],
            reference_wav=str(reference),
            prompt_text=prompt_text,
        )
        target = args.out / f"{draw['name']}.wav"
        sf.write(str(target), samples, loaded.sample_rate, format="WAV")
        records.append(
            {
                "name": draw["name"],
                "file": target.name,
                "mode": args.mode,
                "instruction": instruction,
                "model_text": model_text,
                "prompt_text": prompt_text,
                "seed": draw["seed"],
                "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                **facts,
            }
        )
        print(
            f"  {draw['name']:<22} {facts['audio_s']:>5.2f}s audio  "
            f"{facts['infer_s']:>5.2f}s infer  rtf {facts['rtf']:>5.2f}  "
            f"peak {facts['peak_allocated_mb']:>6.1f}MB"
        )

    payload = {
        "voice": asset.key,
        "line": args.line,
        "mode": args.mode,
        "reference_clip": asset.reference_clip,
        "sample_rate": loaded.sample_rate,
        "control_seeds": list(CONTROL_SEEDS),
        "variant_seed": VARIANT_SEED,
        "draws": records,
    }
    manifest = args.out / "probe.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {len(records)} draws + {manifest.name} into {args.out}")
    print("listen: control_a vs each variant; pollution cross-check: tools/verify_audio.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
