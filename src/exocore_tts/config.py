"""Configuration for the voice factory: paths and the daemon's loopback port.

Environment overrides (all optional):
    EXOCORE_TTS_HOME                    data root                (default: repository root)
    EXOCORE_TTS_VOICE_ROOT              canonical voice assets   (default: <root>/voices)
    EXOCORE_TTS_CANDIDATE_ROOT          casting output root      (default: <root>/candidates)
    EXOCORE_TTS_DOTENV                  cloud API key file override   (default: <root>/.env)
    EXOCORE_TTS_HOST                    bind address             (default: 127.0.0.1)
    EXOCORE_TTS_PORT                    bind port                (default: 8769)
    EXOCORE_TTS_TOKEN                   bearer token             (default: empty = no auth)
    EXOCORE_TTS_IDLE_UNLOAD_SECONDS     idle unload window       (default: 1800; 0 = never)
    EXOCORE_TTS_MAX_TEXT_CHARS          total request text guard (default: 600)

The bind address is a start-up hard gate, not a hint: a non-loopback host is refused even
when a token is configured, because a token is extra protection on loopback -- it does not
make this daemon safe to expose (Plan/0003 §6). Nothing in this package may import Django
or reach into ExoCore.

That last sentence is also true of credentials: the cloud key is read from this repository's
own `.env` (or the process environment), and the sibling ExoCore checkout is never consulted
implicitly (Plan/0005).
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8769
DEFAULT_IDLE_UNLOAD_SECONDS = 1800
DEFAULT_MAX_TEXT_CHARS = 600


class ConfigError(ValueError):
    """The daemon cannot start with this environment: bad value or unsafe bind address."""


@dataclass(frozen=True)
class DaemonConfig:
    """Everything the HTTP port needs; parsed and validated before the server starts."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str = ""
    idle_unload_seconds: int = DEFAULT_IDLE_UNLOAD_SECONDS
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS


def load_daemon_config(env: Mapping[str, str] | None = None) -> DaemonConfig:
    """Read and validate the daemon environment. Raises `ConfigError` on any bad value."""
    source = os.environ if env is None else env
    return DaemonConfig(
        host=loopback_host(source.get("EXOCORE_TTS_HOST", DEFAULT_HOST)),
        port=_int_env(source, "EXOCORE_TTS_PORT", DEFAULT_PORT, minimum=1, maximum=65535),
        token=source.get("EXOCORE_TTS_TOKEN", "").strip(),
        idle_unload_seconds=_int_env(
            source, "EXOCORE_TTS_IDLE_UNLOAD_SECONDS", DEFAULT_IDLE_UNLOAD_SECONDS, minimum=0
        ),
        max_text_chars=_int_env(
            source, "EXOCORE_TTS_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS, minimum=1
        ),
    )


def loopback_host(value: str) -> str:
    """Accept only an explicit loopback address; host names are not trusted.

    ``localhost`` is refused on purpose: it resolves through configuration the daemon does
    not control, and this gate must not be satisfiable by a name that could be pointed
    somewhere else.
    """
    text = value.strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        raise ConfigError(
            f"EXOCORE_TTS_HOST must be a literal loopback address (127.0.0.1 or ::1), got {value!r}"
        ) from None
    if not address.is_loopback:
        raise ConfigError(
            f"refusing to bind non-loopback host {value!r}: loopback is the daemon's network boundary"
        )
    return text


def _int_env(
    source: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int | None = None
) -> int:
    raw = source.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{name} must be {bounds}, got {value}")
    return value


def repo_root() -> Path:
    """Repository root (this file lives at <repo>/src/exocore_tts/config.py)."""
    return Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    override = os.environ.get("EXOCORE_TTS_HOME", "").strip()
    return Path(override).expanduser().resolve() if override else repo_root()


def _resolve(env_name: str, default: Path) -> Path:
    override = os.environ.get(env_name, "").strip()
    return Path(override).expanduser().resolve() if override else default


def voice_root() -> Path:
    """Directory that owns the canonical voice assets (`voices/<key>/`)."""
    return _resolve("EXOCORE_TTS_VOICE_ROOT", _data_root() / "voices")


def candidate_root() -> Path:
    """Default parent directory for casting batches (`candidates/<batch>/`)."""
    return _resolve("EXOCORE_TTS_CANDIDATE_ROOT", _data_root() / "candidates")


def dotenv_path() -> Path:
    """Env file to read the cloud API key from: **this repository's own `.env`**.

    The factory is self-contained (Plan/0005): the sibling ExoCore checkout is never read
    implicitly. `EXOCORE_TTS_DOTENV` points at a different file only as an explicit choice,
    and then it is used alone -- a missing explicit path is an error, not a reason to fall
    back here.
    """
    return _resolve("EXOCORE_TTS_DOTENV", repo_root() / ".env")
