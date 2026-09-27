"""The production engine: frozen voice assets spoken by the local VoxCPM2 model.

This is the only place where a factory voice asset turns into a Vox call (Plan/0003 §4.2):

* the reference clip, prompt transcript and settled generation parameters come from the
  ``VoiceAsset``; the host path never leaves this process;
* the seed is generated here -- callers cannot choose or override it;
* ``delivery`` is refused, and that is a measured outcome, not a stub. The capability probe
  (2026-09-27, `Plan/0003_delivery_probe_result.md`) went through every lever this engine
  has: a text prefix is read aloud in all four forms tried, and steering the clone prompt's
  transcript is not spoken but produces no directed effect and hurt the voice by ear. No safe
  mapping exists, so a non-empty delivery is rejected instead of being quietly dropped.

The engine is constructed at daemon start-up and must stay import-light: torch and voxcpm
are only touched inside ``load``/``synthesize``/``unload`` through ``exocore_tts.voxcpm``.
"""
from __future__ import annotations

import logging
import secrets

from exocore_tts import voxcpm
from exocore_tts.backends.base import AudioResult
from exocore_tts.errors import DeliveryUnsupported, EngineUnavailable
from exocore_tts.text import segment_text
from exocore_tts.voices import DEFAULT_REFERENCE_CLIP, VoiceAsset, voice_dir

logger = logging.getLogger("exocore_tts.voxcpm2")

SEED_CEILING = 1 << 31


class VoxCpm2Backend:
    """Local engine backed by the VoxCPM2 model on this machine's GPU."""

    engine = "voxcpm2"

    def __init__(self, *, model_id: str = voxcpm.MODEL_ID, device: str | None = None) -> None:
        self._model_id = model_id
        self._device = device

    # -- Backend protocol ---------------------------------------------------------------

    def supports_delivery(self) -> bool:
        """Frozen after the measured probe: no safe delivery mapping exists for this engine."""
        return False

    def plan_segments(self, text: str) -> list[str]:
        """The measured local budget decides where one inference ends (Plan/0003 §4.3)."""
        return segment_text(text)

    def check_asset(self, asset: VoiceAsset) -> None:
        """Refuse a damaged asset before a model load is paid for."""
        reference = _reference_path(asset)
        if not reference.is_file():
            raise EngineUnavailable(f"voice {asset.key!r} has no reference clip")
        import soundfile as sf

        try:
            info = sf.info(str(reference))  # header-only read; decoding belongs to casting
        except Exception as exc:
            raise EngineUnavailable(f"voice {asset.key!r} reference clip is unreadable") from exc
        if info.frames <= 0:
            raise EngineUnavailable(f"voice {asset.key!r} reference clip is empty")
        _generation_params(asset)  # a damaged recipe is a damaged asset, not a 500

    def load(self) -> voxcpm.LoadedModel:
        loaded = voxcpm.load_model(self._model_id, self._device)
        logger.info(
            "voxcpm2 ready: sample_rate=%d device=%s",
            loaded.sample_rate,
            _device_name(loaded),
        )
        return loaded

    def unload(self, model: voxcpm.LoadedModel) -> None:
        model.release()

    def synthesize(
        self,
        model: voxcpm.LoadedModel,
        asset: VoiceAsset,
        text: str,
        delivery: str | None,
    ) -> AudioResult:
        if delivery:
            raise DeliveryUnsupported("voxcpm2 has no safe delivery mapping (measured)")
        cfg_value, inference_timesteps = _generation_params(asset)
        samples, facts = voxcpm.generate(
            model,
            text=text,
            cfg_value=cfg_value,
            inference_timesteps=inference_timesteps,
            seed=secrets.randbelow(SEED_CEILING),
            reference_wav=str(_reference_path(asset)),
            prompt_text=asset.prompt_text,
        )
        logger.debug(
            "voxcpm2 segment: infer=%.2fs audio=%.2fs rtf=%.3f peak=%.1fMB",
            facts["infer_s"],
            facts["audio_s"],
            facts["rtf"],
            facts["peak_allocated_mb"],
        )
        return AudioResult(samples=samples, sample_rate=model.sample_rate)


def _reference_path(asset: VoiceAsset):
    return voice_dir(asset.key) / (asset.reference_clip or DEFAULT_REFERENCE_CLIP)


def _generation_params(asset: VoiceAsset) -> tuple[float, int]:
    """Read the settled recipe; unusable values are a damaged asset (503, not a crash)."""
    defaults = asset.generation_defaults or {}
    try:
        cfg_value = float(defaults.get("cfg_value", voxcpm.DEFAULT_CFG))
        raw_timesteps = float(defaults.get("inference_timesteps", voxcpm.DEFAULT_TIMESTEPS))
    except (TypeError, ValueError) as exc:
        raise EngineUnavailable(f"voice {asset.key!r} has unusable generation defaults") from exc
    if not cfg_value > 0 or raw_timesteps < 1 or raw_timesteps != int(raw_timesteps):
        raise EngineUnavailable(f"voice {asset.key!r} has unusable generation defaults")
    return cfg_value, int(raw_timesteps)


def _device_name(loaded: voxcpm.LoadedModel) -> str:
    cuda = getattr(loaded.torch, "cuda", None)
    if cuda is not None and cuda.is_available():
        return str(cuda.get_device_name(0))
    return "cpu"
