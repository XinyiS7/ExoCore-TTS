"""The daemon's stable error vocabulary, and the only place that names wire error codes.

Every failure that can cross the HTTP port is one of these classes. `server.py` renders a
class into exactly one response body -- `{"error": "<code>"}` -- and no exception text ever
travels on the wire; messages exist for the daemon log only.

Codes are frozen by Plan/0003 §2.1. Callers branch on the code, never on a message.
"""
from __future__ import annotations


class TtsError(Exception):
    """Base class for every request-level failure with a stable code and HTTP status."""

    code = "invalid_request"
    status = 422

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.code)
        self.detail = detail


class InvalidRequest(TtsError):
    """Malformed or out-of-bounds request fields (blank text, over-long text/delivery, ...)."""

    code = "invalid_request"
    status = 422


class UnknownVoice(TtsError):
    """A well-formed voice key with no voice asset behind it."""

    code = "unknown_voice"
    status = 404


class DeliveryUnsupported(TtsError):
    """The target backend cannot safely implement a non-empty `delivery`."""

    code = "delivery_unsupported"
    status = 422


class EngineUnavailable(TtsError):
    """No backend for the engine, model load failed, dependency/device missing, or the
    voice asset's manifest/reference is damaged. Never means "still loading"."""

    code = "engine_unavailable"
    status = 503


class SynthesisFailed(TtsError):
    """A request entered synthesis but did not produce valid audio."""

    code = "synthesis_failed"
    status = 500


class Unauthorized(TtsError):
    """A bearer token is configured and the request did not carry it."""

    code = "unauthorized"
    status = 401
