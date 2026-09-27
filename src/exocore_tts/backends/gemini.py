"""Cloud engine: a managed Gemini TTS voice resource, spoken through one paid render.

This backend gives a factory voice asset with ``engine = "gemini"`` its provider voice. Two
rules shape everything here, both frozen by Plan/0004:

* **identity comes from the asset, never from the caller.** The manifest carries the
  provider reference (``cloud_voice``), written only by the managed registration path; a
  missing or malformed reference is refused before any provider call, and there is no
  fallback to a replacement, a prebuilt voice or a freshly created one;
* **one HTTP request is exactly one paid provider render.** ``plan_segments`` hands the
  whole admitted text over in a single piece, and a failure is reported as the port's
  ``synthesis_failed`` instead of being retried on the meter.

The provider SDK and the API key are only touched inside ``load`` -- never at daemon
start-up -- so a machine without the cloud dependency or the key keeps serving the local
engine while the cloud asset fails closed with ``engine_unavailable``.

Style semantics (Addendum A2, verbatim rule): a non-empty ``delivery`` is handed to the
provider exactly as given -- no prefixing, no normalization. With no ``delivery``, the
asset's own ``baseline_style`` speaks for it (some measured voices are only synthesizable
with a style present); an asset without a baseline keeps the original behaviour and sends
no style at all.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from exocore_tts import cloud, voices
from exocore_tts.backends.base import AudioResult
from exocore_tts.errors import EngineUnavailable, SynthesisFailed
from exocore_tts.voices import VoiceAsset

logger = logging.getLogger("exocore_tts.gemini")


@dataclass(frozen=True)
class CloudClientHandle:
    """The runtime's opaque handle: the provider client, nothing on any device."""

    client: cloud.CloudVoiceClient


class GeminiBackend:
    """Gemini TTS production backend for managed cloud voice assets."""

    engine = "gemini"

    def __init__(
        self,
        *,
        model: str = cloud.MODEL,
        dotenv: Path | None = None,
        client_factory: Callable[[], cloud.CloudVoiceClient] | None = None,
    ) -> None:
        self._model = model
        self._dotenv = dotenv
        # Production builds the real client from the environment at load time, so no key is
        # read at daemon start-up; tests inject an offline double here.
        self._client_factory = client_factory or (
            lambda: cloud.CloudVoiceClient.from_env(dotenv, model=model)
        )

    # -- Backend protocol ---------------------------------------------------------------

    def supports_delivery(self) -> bool:
        """The provider takes a natural-language style per request: delivery maps 1:1."""
        return True

    def plan_segments(self, text: str) -> list[str]:
        """One request, one paid render: the whole admitted text goes to the provider once."""
        return [text]

    def check_asset(self, asset: VoiceAsset) -> None:
        """Refuse an asset without a usable provider reference, before any provider call."""
        try:
            voices.cloud_voice_ref(asset)
        except ValueError as exc:
            raise EngineUnavailable(
                f"voice {asset.key!r} has no usable cloud voice reference"
            ) from exc
        if not isinstance(asset.baseline_style, str):
            raise EngineUnavailable(f"voice {asset.key!r} has an unusable baseline style")

    def load(self) -> CloudClientHandle:
        """Build the lightweight provider client; a missing SDK or key fails closed here."""
        cloud.load_sdk()
        try:
            client = self._client_factory()
        except cloud.CloudError as exc:
            raise EngineUnavailable(f"cloud engine unavailable: {exc}") from exc
        return CloudClientHandle(client=client)

    def unload(self, model: CloudClientHandle) -> None:
        """Nothing lives on a device: dropping the handle is the whole cleanup."""

    def synthesize(
        self,
        model: CloudClientHandle,
        asset: VoiceAsset,
        text: str,
        delivery: str | None,
    ) -> AudioResult:
        kind, value = voices.cloud_voice_ref(asset)
        reference = {"voice": value} if kind == "id" else {"prebuilt": value}
        # Verbatim rule: a non-empty delivery crosses this seam untouched. Only when the
        # caller sent none does the asset's own baseline style speak for it.
        style = delivery if delivery else asset.baseline_style
        try:
            data = model.client.render(text, style=style, **reference)
        except cloud.CloudError as exc:
            # Already secret-scrubbed: the log gets the finding, the wire gets a code only.
            logger.error("gemini render failed: %s", exc)
            raise SynthesisFailed("cloud render failed") from exc
        return _decode(data)


def _decode(data: bytes) -> AudioResult:
    """Decode the provider's WAV bytes strictly; unusable audio is a synthesis failure."""
    import numpy as np
    import soundfile as sf

    try:
        samples, sample_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=False)
    except Exception as exc:
        raise SynthesisFailed("cloud render returned unreadable audio") from exc
    samples = np.asarray(samples)
    if samples.size == 0:
        raise SynthesisFailed("cloud render returned no samples")
    return AudioResult(samples=samples, sample_rate=int(sample_rate))
