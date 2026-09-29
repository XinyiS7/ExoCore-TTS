"""Cloud voice path: the *source* side of the factory.

The local VoxCPM2 engine clones references; this module renders those references through a
cloud voice resource. Everything here exists to produce a clip whose transcript is known
exactly, because ultimate cloning needs both halves (audio + what it says).

The API key is never copied into version control and never printed: it is read at call time
from the process environment (`GEMINI_API_KEY`) or from *this repository's own* `.env`
(`GEM_TTS_KEY` first, then the legacy `GEMINI_API_KEY` name). The sibling ExoCore checkout is
never consulted implicitly -- pointing at another file is an explicit `EXOCORE_TTS_DOTENV`
decision (Plan/0005). Any error text on the way out passes through `scrub_secrets`.
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

from . import config

MODEL = "gemini-3.8-flash-tts"
DEFAULT_ENV_VAR = "GEMINI_API_KEY"
# Key-file names in precedence order: the canonical name this repository uses today, then the
# legacy name from before the factory kept its own key file. Both are looked for in one file.
DOTENV_KEY_NAMES = ("GEM_TTS_KEY", "GEMINI_API_KEY")


class CloudError(RuntimeError):
    """Cloud failure with secrets already scrubbed. Message is safe to print."""


def parse_audio_mime(mime_type: str) -> tuple[int, int]:
    """Return `(bits_per_sample, sample_rate)` from e.g. `audio/L16;rate=24000`.

    Every part is inspected, including the family itself: the bits live in `audio/L24`, and
    that token is not a parameter after a semicolon.
    """
    bits, rate = 16, 24000
    for param in str(mime_type).split(";"):
        param = param.strip()
        if param.lower().startswith("rate="):
            try:
                rate = int(param.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif param[:7].lower() == "audio/l":
            try:
                bits = int(param.split("L", 1)[1])
            except (ValueError, IndexError):
                pass
    return bits, rate


def is_pcm_mime(mime_type: str) -> bool:
    """True when the stream is headerless PCM and therefore needs a WAV wrapper."""
    lowered = str(mime_type).lower()
    return "l16" in lowered or "pcm" in lowered


def wrap_pcm_in_wav(pcm: bytes, mime_type: str) -> bytes:
    """Prepend a 44-byte PCM WAV header to a headerless stream."""
    bits, rate = parse_audio_mime(mime_type)
    block = max(1, bits // 8)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 1,
        rate, rate * block, block, bits, b"data", len(pcm),
    )
    return header + pcm


def wav_seconds(data: bytes) -> float:
    """Duration of a PCM WAV byte string; 0.0 when the header cannot be read."""
    try:
        channels = struct.unpack("<H", data[22:24])[0]
        rate = struct.unpack("<I", data[24:28])[0]
        bits = struct.unpack("<H", data[34:36])[0]
    except struct.error:
        return 0.0
    block = max(1, channels * bits // 8)
    return (len(data) - 44) / block / rate


def _clean_env_value(raw: str) -> str:
    """Strip whitespace and one layer of *matching* quotes; empty means unset."""
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text.strip()


def dotenv_value(path: Path, names: tuple[str, ...] = DOTENV_KEY_NAMES) -> str | None:
    """First non-empty value among `names` in a dotenv-style file, or `None` if absent.

    Deliberately literal and bounded: one `NAME=value` per line, CR/LF tolerated, optional
    matching quotes around the value, blank/comment lines ignored, exact name matching (so
    `GEM_TTS_KEY_OLD` is never mistaken for `GEM_TTS_KEY`), first non-empty value per name
    wins, and no `export`/expansion/override semantics. A missing file is `None`, not an
    error -- the caller decides how loudly to fail. No environment is consulted here.
    """
    if not path.is_file():
        return None
    wanted = set(names)
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw_value = line.partition("=")
        name = name.strip()
        if name in wanted and name not in found:
            value = _clean_env_value(raw_value)
            if value:
                found[name] = value
    for name in names:
        if name in found:
            return found[name]
    return None


def read_api_key(dotenv: Path | None = None, env_var: str = DEFAULT_ENV_VAR) -> str:
    """The one cloud-key reader: the process environment first, then this repository's `.env`.

    Precedence (frozen by Plan/0005): a non-empty process `env_var` wins; otherwise the
    resolved key file -- an explicit `dotenv` argument, else `EXOCORE_TTS_DOTENV`, else
    `<repo>/.env` -- is read for the canonical `GEM_TTS_KEY` and then the legacy
    `GEMINI_API_KEY`. The value is never logged, and the error names only the variable and
    the path.
    """
    value = os.environ.get(env_var, "").strip()
    if value:
        return value
    path = Path(dotenv) if dotenv else config.dotenv_path()
    found = dotenv_value(path)
    if found:
        return found
    names = " or ".join(DOTENV_KEY_NAMES)
    raise CloudError(
        f"no {env_var} in the environment and no {names} in {path}; "
        "keep the key in this repository's own .env or export it explicitly"
    )


def load_sdk() -> None:
    """Import the provider SDK on demand; a missing dependency is a `CloudError`.

    The daemon has to keep starting and serving the local engine on a machine where the
    cloud dependency is not installed, so this import happens only when a cloud render is
    actually about to be attempted. Its absence is the port's `engine_unavailable`, never an
    ImportError escaping a backend.
    """
    try:
        from google import genai  # noqa: F401 - the import itself is the check
        from google.genai import types  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - any import failure means "no SDK here"
        raise CloudError("google-genai is not installed") from exc


def scrub_secrets(text: str, secret: str) -> str:
    """Strip a key (and the fragments SDKs like to quote back) out of a message."""
    for fragment in (secret, secret[:12], secret[-8:]):
        if fragment:
            text = text.replace(fragment, "***")
    return text


class CloudVoiceClient:
    """The two cloud operations the factory needs: render a clip, create a voice resource."""

    def __init__(self, api_key: str, *, model: str = MODEL) -> None:
        self._api_key = api_key
        self.model = model

    @classmethod
    def from_env(cls, dotenv: Path | None = None, *, model: str = MODEL) -> CloudVoiceClient:
        return cls(read_api_key(dotenv), model=model)

    def render(
        self,
        text: str,
        *,
        voice: str = "",
        prebuilt: str = "",
        style: str = "",
        temperature: float = 1.0,
    ) -> bytes:
        """Render one clip and return WAV bytes (wrapped when the stream is raw PCM)."""
        if not voice and not prebuilt:
            raise CloudError("render needs either a voice resource id or a prebuilt voice name")
        from google import genai
        from google.genai import types

        part = types.Part(text=text)
        if style:
            part = types.Part(text=text, speech_metadata=types.SpeechMetadata(style=style))
        voice_config = (
            types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=prebuilt))
            if prebuilt
            else types.VoiceConfig(voice=voice)
        )
        request = types.GenerateContentConfig(
            temperature=temperature,
            response_modalities=["audio"],
            speech_config=types.SpeechConfig(voice_config=voice_config),
        )
        audio, mime = bytearray(), ""
        try:
            client = genai.Client(api_key=self._api_key)
            stream = client.models.generate_content_stream(
                model=self.model,
                contents=[types.Content(role="user", parts=[part])],
                config=request,
            )
            for chunk in stream:
                inline = chunk.parts[0].inline_data if chunk.parts else None
                if inline and inline.data:
                    audio.extend(inline.data)
                    mime = inline.mime_type
        except Exception as exc:  # noqa: BLE001 - the message is the finding, scrubbed below
            raise CloudError(scrub_secrets(f"{type(exc).__name__}: {exc}", self._api_key)) from exc
        if not audio:
            raise CloudError("cloud render returned no audio")
        data = bytes(audio)
        return wrap_pcm_in_wav(data, mime) if is_pcm_mime(mime) else data

    def create_voice(self, display_name: str, prompt: str) -> str:
        """Create a prompted (designed) voice resource from a style prompt; returns its id."""
        from google import genai

        try:
            client = genai.Client(api_key=self._api_key)
            created = client.voices.create(
                store=True,
                voice={
                    "type": "prompted",
                    "display_name": display_name,
                    "model": self.model,
                    "prompted": {"input": prompt},
                },
            )
        except Exception as exc:  # noqa: BLE001 - the message is the finding, scrubbed below
            raise CloudError(scrub_secrets(f"{type(exc).__name__}: {exc}", self._api_key)) from exc
        voice_id = getattr(created, "id", None) or getattr(created, "name", None)
        if not voice_id:
            raise CloudError(f"voice create returned no id: {scrub_secrets(str(created), self._api_key)[:200]}")
        return str(voice_id)
