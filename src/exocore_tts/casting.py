"""VoxCPM2 voice casting bench: candidate planning, batch synthesis, manifest records.

Run it with this service's own interpreter (never inside Django):

    E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .
    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py design \
        --designs tools/sandro_designs.txt --lines tools/sandro_lines.txt --out candidates/round1

Why it is shaped like this:

* **Blind listening** - candidate files are numbered (`cand_0001.wav`); the design description,
  seed and every knob live in `manifest.json`. An ID never tells you which design it came from,
  so you pick with your ears instead of your memory.
* **Reproducible** - every entry records cfg / timesteps / seed / the exact text handed to the
  model, so a winner can be regenerated or a loser inspected later.
* **Resumable** - the manifest is rewritten after each candidate; re-running the same command
  continues instead of redoing finished work (already-finished IDs are skipped).
* **Cheap restarts** - the model is loaded once per batch, and only if there is pending work.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from exocore_tts.config import candidate_root
from exocore_tts.voices import VoiceAsset, list_voices, save_voice, validate_key

MODEL_ID = "openbmb/VoxCPM2"
MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1

MODE_DESIGN = "design"
MODE_CLONE = "clone"
MODES = (MODE_DESIGN, MODE_CLONE)

DEFAULT_CFG = 2.0
DEFAULT_TIMESTEPS = 10
DEFAULT_SEED_BASE = 1000
# Measured on the RTX 3060 Ti against ~6.8 GB usable VRAM: 94 chars peaked at ~6.4 GB reserved.
DEFAULT_MAX_CHARS = 120


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_id(index: int) -> str:
    """Stable, opinion-free identifier: `cand_0001`, `cand_0002`, ..."""
    return f"cand_{index + 1:04d}"


def normalize_candidate_id(raw: str) -> str:
    """Accept `7`, `cand_7` or `cand_0007` on the command line."""
    text = str(raw).strip().lower()
    if text.startswith("cand_"):
        text = text[5:]
    if not text.isdigit():
        raise ValueError(f"Not a candidate id: {raw!r}")
    return candidate_id(int(text) - 1)


@dataclass(frozen=True)
class CandidateSpec:
    """One planned synthesis plus everything required to reproduce it."""

    candidate_id: str
    mode: str
    line: str
    model_text: str
    design: str
    reference_wav: str
    prompt_text: str
    cfg_value: float
    inference_timesteps: int
    seed: int

    @property
    def filename(self) -> str:
        return f"{self.candidate_id}.wav"


def read_lines(path: Path) -> list[str]:
    """Read candidate lines: one per line, `#` comments and blanks ignored."""
    lines = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if text and not text.startswith("#"):
            lines.append(text)
    return lines


def build_plan(
    *,
    lines: Sequence[str],
    mode: str = MODE_DESIGN,
    designs: Sequence[str] = (),
    reference_wav: str = "",
    prompt_text: str = "",
    repeats: int = 1,
    cfg_value: float = DEFAULT_CFG,
    inference_timesteps: int = DEFAULT_TIMESTEPS,
    seed_base: int = DEFAULT_SEED_BASE,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[CandidateSpec]:
    """Expand (designs x lines x repeats) - or (lines x repeats) for clone mode - into a plan."""
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected one of {MODES}")
    if repeats < 1:
        raise ValueError("--repeats must be >= 1")

    clean_lines = [line.strip() for line in lines if line and line.strip()]
    if not clean_lines:
        raise ValueError("No lines to speak: provide --lines FILE with at least one line")
    if max_chars > 0:
        too_long = [line for line in clean_lines if len(line) > max_chars]
        if too_long:
            raise ValueError(
                f"{len(too_long)} line(s) exceed --max-chars {max_chars} "
                f"(e.g. {too_long[0][:40]!r}); split them or raise the guard deliberately"
            )

    bases: list[dict] = []
    if mode == MODE_DESIGN:
        if reference_wav:
            raise ValueError("--reference belongs to clone mode, not design mode")
        clean_designs = [d.strip() for d in designs if d and d.strip()]
        if not clean_designs:
            raise ValueError("design mode needs at least one --design (or --designs FILE)")
        for design in clean_designs:
            for line in clean_lines:
                for _ in range(repeats):
                    bases.append(
                        {
                            "mode": mode,
                            "line": line,
                            "model_text": f"({design}){line}",
                            "design": design,
                            "reference_wav": "",
                            "prompt_text": "",
                        }
                    )
    else:
        if designs:
            raise ValueError("design descriptions belong to design mode, not clone mode")
        if not reference_wav:
            raise ValueError("clone mode needs --reference WAV")
        clip = Path(reference_wav).expanduser()
        if not clip.is_file():
            raise ValueError(f"Reference clip not found: {clip}")
        clip_path = str(clip.resolve())
        for line in clean_lines:
            for _ in range(repeats):
                bases.append(
                    {
                        "mode": mode,
                        "line": line,
                        "model_text": line,
                        "design": "",
                        "reference_wav": clip_path,
                        "prompt_text": prompt_text.strip(),
                    }
                )

    return [
        CandidateSpec(
            candidate_id=candidate_id(index),
            seed=seed_base + index,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            **base,
        )
        for index, base in enumerate(bases)
    ]


class Manifest:
    """Append-only batch record; every write is atomic and incremental."""

    def __init__(self, path: Path, payload: dict):
        self.path = path
        self.payload = payload

    @classmethod
    def load_or_create(cls, batch_dir: Path, *, model_id: str = MODEL_ID) -> "Manifest":
        batch_dir.mkdir(parents=True, exist_ok=True)
        path = batch_dir / MANIFEST_NAME
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        else:
            payload = {
                "schema_version": SCHEMA_VERSION,
                "model_id": model_id,
                "engine": "voxcpm2",
                "created_at": _now(),
                "updated_at": _now(),
                "run_info": {},
                "entries": [],
            }
        return cls(path, payload)

    @property
    def entries(self) -> list[dict]:
        return list(self.payload.get("entries", []))

    def finished_ids(self) -> set[str]:
        return {entry["candidate_id"] for entry in self.entries}

    def set_run_info(self, **info) -> None:
        self.payload["run_info"] = {**self.payload.get("run_info", {}), **info}
        self.payload["updated_at"] = _now()
        self._save()

    def append(self, entry: dict) -> None:
        self.payload.setdefault("entries", []).append(entry)
        self.payload["updated_at"] = _now()
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp, self.path)


def _load_model(model_id: str = MODEL_ID, device: str | None = None):
    """Load VoxCPM2 once per batch. Heavy: torch/voxcpm are imported here and nowhere else."""
    import torch
    from voxcpm import VoxCPM

    if device is None and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is not available in this interpreter; run casting with the service's own env "
            "(E:/Miniconda3/envs/voxcpm_runtime/python.exe)"
        )
    model = VoxCPM.from_pretrained(model_id, load_denoiser=False, device=device)
    sample_rate = int(
        getattr(model, "sample_rate", getattr(getattr(model, "tts_model", None), "sample_rate", 48000))
    )
    return model, sample_rate


def _synthesize(model, spec: CandidateSpec, *, sample_rate: int, target: Path) -> dict:
    """Synthesize one candidate to `target`; returns the measured facts."""
    import soundfile as sf
    import torch

    torch.manual_seed(spec.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(spec.seed)
        torch.cuda.reset_peak_memory_stats()

    kwargs = {
        "text": spec.model_text,
        "cfg_value": spec.cfg_value,
        "inference_timesteps": spec.inference_timesteps,
    }
    if spec.reference_wav:
        kwargs["reference_wav_path"] = spec.reference_wav
        if spec.prompt_text:
            # Ultimate cloning: same clip as prompt + its transcript raises similarity.
            kwargs["prompt_wav_path"] = spec.reference_wav
            kwargs["prompt_text"] = spec.prompt_text

    started = time.perf_counter()
    with torch.inference_mode():
        wav = model.generate(**kwargs)
    infer_s = time.perf_counter() - started

    tmp_target = target.with_name(target.name + ".tmp")
    sf.write(str(tmp_target), wav, sample_rate)
    os.replace(tmp_target, target)

    audio_s = round(len(wav) / sample_rate, 3)
    peak_mb = round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1) if torch.cuda.is_available() else 0.0
    return {
        "infer_s": round(infer_s, 2),
        "audio_s": audio_s,
        "rtf": round(infer_s / audio_s, 3) if audio_s > 0 else 0.0,
        "peak_allocated_mb": peak_mb,
        "sample_rate": sample_rate,
    }


def run_batch(
    batch_dir: Path,
    plan: Sequence[CandidateSpec],
    *,
    model_id: str = MODEL_ID,
    device: str | None = None,
    dry_run: bool = False,
) -> int:
    """Synthesize every pending candidate into `batch_dir`. Returns the number written."""
    batch_dir = Path(batch_dir)
    manifest = Manifest.load_or_create(batch_dir, model_id=model_id)
    finished = manifest.finished_ids()
    pending = [spec for spec in plan if spec.candidate_id not in finished]

    print(f"batch      : {batch_dir}")
    print(f"manifest   : {manifest.path}")
    print(f"planned    : {len(plan)} candidate(s), {len(plan) - len(pending)} already done, {len(pending)} pending")

    if dry_run:
        for spec in plan:
            state = "skip" if spec.candidate_id in finished else "todo"
            label = spec.design or Path(spec.reference_wav).name
            print(f"  [{state}] {spec.candidate_id} seed={spec.seed} design={label[:48]!r} line={spec.line[:32]!r}")
        return 0

    if not pending:
        print("nothing to do: every planned candidate is already in the manifest")
        return 0

    print("loading model (this is the slow part, once per batch) ...", flush=True)
    load_started = time.perf_counter()
    model, sample_rate = _load_model(model_id, device)
    load_s = round(time.perf_counter() - load_started, 2)
    print(f"model ready in {load_s}s  sample_rate={sample_rate}")

    import torch

    manifest.set_run_info(
        model_id=model_id,
        sample_rate=sample_rate,
        load_s=load_s,
        torch=torch.__version__,
        cuda=torch.version.cuda,
        device=torch.cuda.get_device_name(0) if torch.cuda.is_available() else device or "unknown",
    )

    written = 0
    for position, spec in enumerate(pending, start=1):
        target = batch_dir / spec.filename
        print(f"[{position}/{len(pending)}] {spec.candidate_id} seed={spec.seed} {spec.line[:36]!r}", end=" ", flush=True)
        try:
            stats = _synthesize(model, spec, sample_rate=sample_rate, target=target)
        except Exception:
            print("FAILED")
            print(f"\nStopped after {written} candidate(s). {manifest.path} already records them;")
            print("fix the cause and re-run the same command to continue where this stopped.")
            raise
        manifest.append(
            {
                "candidate_id": spec.candidate_id,
                "file": spec.filename,
                "mode": spec.mode,
                "design": spec.design,
                "reference_wav": spec.reference_wav,
                "prompt_text": spec.prompt_text,
                "line": spec.line,
                "model_text": spec.model_text,
                "cfg_value": spec.cfg_value,
                "inference_timesteps": spec.inference_timesteps,
                "seed": spec.seed,
                "created_at": _now(),
                **stats,
            }
        )
        written += 1
        print(f"-> {stats['infer_s']}s  audio {stats['audio_s']}s  RTF {stats['rtf']}  peak {stats['peak_allocated_mb']}MB")

    print(f"\nwrote {written} candidate(s) into {batch_dir}")
    print("listen, then freeze the winner:  python tools/cast.py pick --from " f"{batch_dir} --id <N> --key <voice_key>")
    return written


def _open_existing_batch(batch_dir: Path) -> Manifest:
    """Open a batch that is expected to exist. Never invents an empty batch from a typo."""
    manifest_path = Path(batch_dir) / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No casting batch at {manifest_path}; run `cast.py design` or `cast.py clone` first")
    return Manifest.load_or_create(Path(batch_dir))


def pick_candidate(
    batch_dir: Path,
    raw_id: str,
    key: str,
    *,
    display_name: str = "",
    style: str = "",
    force: bool = False,
) -> VoiceAsset:
    """Freeze one finished candidate into `voices/<key>/`."""
    batch_dir = Path(batch_dir)
    manifest = _open_existing_batch(batch_dir)
    wanted = normalize_candidate_id(raw_id)
    entry = next((e for e in manifest.entries if e["candidate_id"] == wanted), None)
    if entry is None:
        raise KeyError(f"{wanted} is not in {manifest.path}; finished: {sorted(manifest.finished_ids())}")

    clip = batch_dir / entry["file"]
    if not clip.is_file():
        raise FileNotFoundError(f"Candidate audio missing on disk: {clip}")

    validate_key(key)
    asset = VoiceAsset(
        key=key,
        display_name=display_name or key,
        engine="voxcpm2",
        baseline_instruction=style,
        prompt_text=entry.get("line", ""),
        generation_defaults={
            "cfg_value": entry.get("cfg_value", DEFAULT_CFG),
            "inference_timesteps": entry.get("inference_timesteps", DEFAULT_TIMESTEPS),
        },
        source={
            "batch": str(batch_dir),
            "candidate_id": entry["candidate_id"],
            "model_id": manifest.payload.get("model_id", MODEL_ID),
            "design": entry.get("design", ""),
            "seed": entry.get("seed"),
            "cfg_value": entry.get("cfg_value"),
            "inference_timesteps": entry.get("inference_timesteps"),
            "frozen_at": _now(),
        },
    )
    target_dir = save_voice(asset, clip, force=force)
    print(f"voice  : {asset.key} ({asset.display_name})")
    print(f"frozen : {target_dir}")
    print(f"clip   : {target_dir / asset.reference_clip}  (source {entry['candidate_id']})")
    print(f"prompt : {asset.prompt_text!r}")
    if asset.source["design"]:
        print(f"design : {asset.source['design']}")
    print("next   : bind it in ExoCore (VoiceProfile.name = this key) once the daemon serves it")
    return asset


def list_batch(batch_dir: Path) -> int:
    """Print the finished candidates of a batch, for the by-ear picking step."""
    manifest = _open_existing_batch(Path(batch_dir))
    entries = manifest.entries
    if not entries:
        print(f"{manifest.path} has no finished candidates yet")
        return 0
    print(f"{len(entries)} candidate(s) in {manifest.path}")
    for entry in entries:
        design = entry.get("design") or Path(entry.get("reference_wav", "")).name or "-"
        print(
            f"  {entry['candidate_id']}  {entry.get('audio_s', 0):>6.2f}s  "
            f"RTF {entry.get('rtf', 0):>5}  {design[:40]!r}  {entry['line'][:28]!r}"
        )
    return 0


def list_voice_assets() -> int:
    """Print the frozen voices of this factory."""
    assets = list_voices()
    if not assets:
        print("no frozen voices yet")
        return 0
    print(f"{len(assets)} frozen voice(s):")
    for asset in assets:
        print(f"  {asset.key:<20} engine={asset.engine:<8} prompt={asset.prompt_text[:28]!r}")
    return 0


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lines", required=True, type=Path, help="file with one spoken line per row")
    parser.add_argument("--out", required=True, type=Path, help=f"batch directory (default parent: {candidate_root()})")
    parser.add_argument("--repeats", type=int, default=1, help="how many draws per (design, line); voice design is not deterministic")
    parser.add_argument("--cfg", dest="cfg_value", type=float, default=DEFAULT_CFG)
    parser.add_argument("--timesteps", dest="inference_timesteps", type=int, default=DEFAULT_TIMESTEPS)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS, help="refuse longer lines (VRAM guard)")
    parser.add_argument("--device", default=None, help="torch device, e.g. cuda:0 (default: auto)")
    parser.add_argument("--model", default=MODEL_ID, help="VoxCPM model id or local snapshot path")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without loading the model")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cast.py",
        description="VoxCPM2 voice casting bench (runs in the service's own conda env)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    design = subparsers.add_parser(MODE_DESIGN, help="invent a new voice from a text description")
    design.add_argument("--designs", required=True, type=Path, help="file with one design description per row")
    _add_run_arguments(design)

    clone = subparsers.add_parser(MODE_CLONE, help="draw candidates from a reference clip")
    clone.add_argument("--reference", required=True, help="reference WAV to clone (16 kHz+ clean clip)")
    clone.add_argument("--prompt-text", default="", help="transcript of the reference clip (raises similarity)")
    _add_run_arguments(clone)

    pick = subparsers.add_parser("pick", help="freeze a finished candidate into voices/<key>/")
    pick.add_argument("--from", dest="batch_dir", required=True, help="batch directory holding manifest.json")
    pick.add_argument("--id", dest="raw_id", required=True, help="candidate id, e.g. 7 or cand_0007")
    pick.add_argument("--key", required=True, help="voice key, e.g. sandro_v1")
    pick.add_argument("--display-name", default="")
    pick.add_argument("--style", default="", help="baseline style description stored with the voice")
    pick.add_argument("--force", action="store_true", help="replace an existing voice with the same key")

    list_cmd = subparsers.add_parser("list", help="inspect a batch, or the frozen voices when --from is omitted")
    list_cmd.add_argument("--from", dest="batch_dir", default="", help="batch directory to inspect")
    list_cmd.add_argument("--voices", action="store_true", help="list frozen voices instead of a batch")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "pick":
        pick_candidate(
            Path(args.batch_dir),
            args.raw_id,
            args.key,
            display_name=args.display_name,
            style=args.style,
            force=args.force,
        )
        return 0

    if args.command == "list":
        if args.voices or not args.batch_dir:
            return list_voice_assets()
        return list_batch(Path(args.batch_dir))

    designs = read_lines(args.designs) if args.command == MODE_DESIGN else []
    plan = build_plan(
        lines=read_lines(args.lines),
        mode=args.command,
        designs=designs,
        reference_wav=getattr(args, "reference", ""),
        prompt_text=getattr(args, "prompt_text", ""),
        repeats=args.repeats,
        cfg_value=args.cfg_value,
        inference_timesteps=args.inference_timesteps,
        seed_base=args.seed_base,
        max_chars=args.max_chars,
    )
    run_batch(Path(args.out), plan, model_id=args.model, device=args.device, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
