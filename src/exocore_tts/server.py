"""The HTTP port: frozen contract, bearer gate, stable error codes (Plan/0003 §2).

This layer stays thin on purpose. It knows two endpoints, one request shape and one error
shape; voice assets, segmentation, engines and model lifetime all live below it. The only
body it ever produces for a failure is ``{"error": "<code>"}`` -- no ``detail``, no extra
keys, no raw exception text (the daemon log keeps the details).

Configuration is environment-only (Plan/0003 §6) and the bind address is validated before
the socket exists: a non-loopback host refuses to start.
"""
from __future__ import annotations

import logging
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from exocore_tts.backends import Backend, default_backends
from exocore_tts.config import ConfigError, DaemonConfig, load_daemon_config
from exocore_tts.errors import TtsError
from exocore_tts.service import TtsService

logger = logging.getLogger("exocore_tts.server")

BEARER_PREFIX = "bearer "


class TtsRequest(BaseModel):
    """The only accepted request shape; undeclared fields are refused, not ignored."""

    model_config = ConfigDict(extra="forbid")

    text: str
    voice_key: str
    delivery: str = ""


def create_app(config: DaemonConfig, service: TtsService) -> FastAPI:
    """Build the daemon app. Tests inject their own service; production uses `build_service`."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        service.close()

    app = FastAPI(
        title="ExoCore TTS",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def bearer_guard(request: Request, call_next):
        if config.token and not _authorized(request, config.token):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    @app.exception_handler(TtsError)
    async def tts_error_handler(_: Request, exc: TtsError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": exc.code})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.exception_handler(StarletteHTTPException)
    async def framework_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Out-of-contract paths (unknown route, wrong method) keep the same single-field
        # shape with a best-effort code; the frozen table only documents the two endpoints.
        if exc.status_code == 401:
            code = "unauthorized"
        elif exc.status_code >= 500:
            code = "synthesis_failed"
        else:
            code = "invalid_request"
        return JSONResponse(status_code=exc.status_code, content={"error": code})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        logger.exception("unhandled daemon error")
        return JSONResponse(status_code=500, content={"error": "synthesis_failed"})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "state": service.health_state()}

    @app.post("/tts")
    def tts(payload: TtsRequest) -> Response:
        audio = service.synthesize(
            text=payload.text,
            voice_key=payload.voice_key,
            delivery=payload.delivery,
        )
        return Response(content=audio, media_type="audio/wav")

    return app


def build_service(config: DaemonConfig) -> tuple[TtsService, dict[str, Backend]]:
    """Production wiring: the engines this repository actually ships."""
    backends = default_backends()
    service = TtsService(
        backends,
        max_text_chars=config.max_text_chars,
        idle_unload_seconds=config.idle_unload_seconds,
    )
    return service, backends


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point. Configuration comes from the environment, never from flags."""
    try:
        config = load_daemon_config()
    except ConfigError as exc:
        print(f"exocore-tts: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    service, backends = build_service(config)
    app = create_app(config, service)
    logger.info(
        "serving on http://%s:%d (auth: %s; engines: %s; single process only)",
        config.host,
        config.port,
        "bearer" if config.token else "none",
        ", ".join(sorted(backends)) or "none",
    )
    import uvicorn

    uvicorn.run(app, host=config.host, port=config.port, workers=1)
    return 0


def _authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(BEARER_PREFIX):
        return False
    provided = header[len(BEARER_PREFIX) :].strip()
    return secrets.compare_digest(provided.encode("utf-8"), token.encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
