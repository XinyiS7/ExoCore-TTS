"""The shared low-level VoxCPM2 entry point for casting and the daemon (Plan/0003 §4.2).

Both consumers speak to the model through this module and nowhere else:

* casting draws candidates and records the measured facts in its manifest -- it owns the
  candidate files, the manifest and the CLI;
* the daemon's ``voxcpm2`` backend renders straight into memory -- it owns the voice assets
  and never writes a temporary file.

``torch`` and ``voxcpm`` stay lazily imported: the daemon must be able to start, answer
``/health`` and serve other engines without paying the heavy import. The armed handle
carries the imported torch module so releasing device memory does not need a second import.
"""
from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from typing import Any

MODEL_ID = "openbmb/VoxCPM2"

# Settled generation defaults, used when neither a caller (casting CLI) nor a voice asset
# carries its own values.
DEFAULT_CFG = 2.0
DEFAULT_TIMESTEPS = 10

# Only reached if a model object exposes no sample rate at all.
FALLBACK_SAMPLE_RATE = 48000


@dataclass
class LoadedModel:
    """A loaded model plus the facts needed to describe and release it."""

    model: Any
    sample_rate: int
    torch: Any

    def release(self) -> None:
        """Drop the model reference and hand device memory back to the machine.

        Runs on the runtime worker thread; the caller drops its own handle reference right
        after, and the CUDA cache is emptied so an idle daemon does not sit on VRAM.
        """
        self.model = None
        gc.collect()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


def _runtime():
    """Import the heavy stack. The only place ``torch``/``voxcpm`` are imported."""
    import torch
    from voxcpm import VoxCPM

    return torch, VoxCPM


def load_model(model_id: str = MODEL_ID, device: str | None = None) -> LoadedModel:
    """Load VoxCPM2 once. Heavy: the caller decides when the machine can afford it."""
    torch, voxcpm_class = _runtime()
    if device is None and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is not available in this interpreter; run the voice factory with its own env "
            "(E:/Miniconda3/envs/voxcpm_runtime/python.exe)"
        )
    model = voxcpm_class.from_pretrained(model_id, load_denoiser=False, device=device)
    sample_rate = int(
        getattr(
            model,
            "sample_rate",
            getattr(getattr(model, "tts_model", None), "sample_rate", FALLBACK_SAMPLE_RATE),
        )
    )
    return LoadedModel(model=model, sample_rate=sample_rate, torch=torch)


def generate(
    loaded: LoadedModel,
    *,
    text: str,
    cfg_value: float,
    inference_timesteps: int,
    seed: int,
    reference_wav: str = "",
    prompt_text: str = "",
) -> tuple[Any, dict]:
    """Run one generation and return ``(samples, measured facts)``.

    No file IO: casting writes the candidate itself, the daemon keeps the samples in memory.
    The returned facts are exactly the ones casting records in its manifest, so a manifest
    written before and after this refactor stays identical in shape and meaning.
    """
    torch = loaded.torch
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
        torch.cuda.reset_peak_memory_stats()

    kwargs: dict[str, Any] = {
        "text": text,
        "cfg_value": cfg_value,
        "inference_timesteps": inference_timesteps,
    }
    if reference_wav:
        kwargs["reference_wav_path"] = reference_wav
        if prompt_text:
            # Ultimate cloning: same clip as prompt + its transcript raises similarity.
            kwargs["prompt_wav_path"] = reference_wav
            kwargs["prompt_text"] = prompt_text

    started = time.perf_counter()
    with torch.inference_mode():
        samples = loaded.model.generate(**kwargs)
    infer_s = time.perf_counter() - started

    audio_s = round(len(samples) / loaded.sample_rate, 3)
    peak_mb = (
        round(torch.cuda.max_memory_allocated() / (1024 ** 2), 1)
        if torch.cuda.is_available()
        else 0.0
    )
    facts = {
        "infer_s": round(infer_s, 2),
        "audio_s": audio_s,
        "rtf": round(infer_s / audio_s, 3) if audio_s > 0 else 0.0,
        "peak_allocated_mb": peak_mb,
        "sample_rate": loaded.sample_rate,
    }
    return samples, facts
