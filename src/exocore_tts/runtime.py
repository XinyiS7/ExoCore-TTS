"""Single-worker model runtime: one GPU, one queue, one load owner (Plan/0003 §5).

Everything heavy -- model load, inference, device cleanup -- runs on one worker thread, so
two renders can never share the single local GPU and the HTTP event loop never blocks on a
model. The runtime is also the only place that decides to load or evict a model:

* ``cold -> loading -> ready`` on the first request; a failed load rolls back to ``cold``
  instead of pinning the daemon in ``loading``;
* concurrent first requests produce exactly one load because the load itself happens on
  the worker, behind the queue;
* eviction re-validates under the same lock that request admission uses, so a decision
  taken from an older snapshot can never drop a model a request is already waiting for.

The published ``state`` and the model reference are always mutated together under one lock:
there is no window in which ``/health`` can report ``ready`` while the reference is empty.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Callable, TypeVar

from exocore_tts.backends.base import Backend
from exocore_tts.errors import EngineUnavailable

logger = logging.getLogger("exocore_tts.runtime")

T = TypeVar("T")

MIN_POLL_SECONDS = 0.02
MAX_POLL_SECONDS = 60.0


class RuntimeState(str, Enum):
    """The only public health vocabulary: heavy model absent, arriving, or usable."""

    COLD = "cold"
    LOADING = "loading"
    READY = "ready"


class LoadFailure(EngineUnavailable):
    """The heavy model could not be loaded; the runtime has already rolled back to cold."""


class ModelRuntime:
    """Serializes load / synthesize / evict for one backend on one worker thread."""

    def __init__(
        self,
        backend: Backend,
        *,
        idle_unload_seconds: float = 0.0,
        idle_poll_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._idle_unload_seconds = float(idle_unload_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._state = RuntimeState.COLD
        self._model: Any = None
        self._pending = 0
        self._last_activity = self._clock()
        self._closed = False
        self._poll_seconds = MAX_POLL_SECONDS
        self._local = threading.local()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"tts-{backend.engine}"
        )
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        if self._idle_unload_seconds > 0:
            poll = idle_poll_seconds if idle_poll_seconds is not None else self._default_poll()
            self._poll_seconds = max(MIN_POLL_SECONDS, float(poll))
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                name=f"tts-{backend.engine}-idle",
                daemon=True,
            )
            self._monitor.start()

    # -- public surface -----------------------------------------------------------------

    def state(self) -> RuntimeState:
        """Cheap snapshot for ``/health``; never waits for the worker."""
        with self._lock:
            return self._state

    def run(self, work: Callable[[Any], T]) -> T:
        """Admit one request, run ``work(model)`` on the worker, return its result.

        Admission (pending count and activity time) is registered under the shared lock
        before the job is queued, and refreshed when it finishes. Calling this again from
        the worker thread itself would wait on a queue it is blocking, so that misuse is
        refused loudly instead of hanging.
        """
        if getattr(self._local, "in_worker", False):
            raise RuntimeError(
                "ModelRuntime.run() cannot be called from the runtime's own worker thread"
            )
        with self._lock:
            self._pending += 1
            self._last_activity = self._clock()
        try:
            return self._executor.submit(self._execute, work).result()
        finally:
            with self._lock:
                self._pending -= 1
                self._last_activity = self._clock()

    def evict_if_idle(self) -> bool:
        """Re-validate ownership and evict the model if it is still genuinely idle.

        Called by the idle monitor's worker job and by tests. It never acts on a snapshot:
        every condition is re-checked under the shared lock, so a request admitted after
        the monitor started looking -- or a render still in flight -- cancels the eviction.
        """
        with self._lock:
            if self._idle_unload_seconds <= 0:
                return False
            if self._state is not RuntimeState.READY or self._model is None:
                return False
            if self._pending > 0:
                return False
            if self._clock() - self._last_activity < self._idle_unload_seconds:
                return False
            model = self._model
            # State and reference flip together: never "ready" without a model.
            self._model = None
            self._state = RuntimeState.COLD
        self._release(model)
        logger.info(
            "engine %s evicted after %.0fs idle", self._backend.engine, self._idle_unload_seconds
        )
        return True

    def poll_idle_once(self) -> None:
        """One idle-monitor tick: cheap look here, heavy eviction on the worker thread."""
        with self._lock:
            candidate = (
                self._idle_unload_seconds > 0
                and self._state is RuntimeState.READY
                and self._model is not None
                and self._pending == 0
                and self._clock() - self._last_activity >= self._idle_unload_seconds
            )
        if candidate:
            self._executor.submit(self.evict_if_idle)

    def close(self) -> None:
        """Stop the idle monitor, drain the worker and release the model. Idempotent."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=5.0)
        self._executor.shutdown(wait=True)
        with self._lock:
            model = self._model
            self._model = None
            self._state = RuntimeState.COLD
        if model is not None:
            self._release(model)

    # -- worker-thread internals ---------------------------------------------------------

    def _execute(self, work: Callable[[Any], T]) -> T:
        self._local.in_worker = True
        model = self._ensure_loaded()
        return work(model)

    def _ensure_loaded(self) -> Any:
        """Load exactly once. Worker thread only, so a single load owner is structural."""
        with self._lock:
            if self._state is RuntimeState.READY:
                return self._model
            # LOADING is published before the heavy call and can only be observed here as
            # READY again: this method runs on the worker, the only thread that loads.
            self._state = RuntimeState.LOADING
        started = self._clock()
        try:
            model = self._backend.load()
        except Exception as exc:
            with self._lock:
                self._model = None
                self._state = RuntimeState.COLD
            logger.error("engine %s load failed: %s", self._backend.engine, exc, exc_info=True)
            raise LoadFailure(f"model load failed for engine {self._backend.engine!r}") from exc
        with self._lock:
            self._model = model
            self._state = RuntimeState.READY
        logger.info("engine %s ready in %.2fs", self._backend.engine, self._clock() - started)
        return model

    def _release(self, model: Any) -> None:
        try:
            self._backend.unload(model)
        except Exception as exc:
            logger.error("engine %s unload failed: %s", self._backend.engine, exc, exc_info=True)

    def _monitor_loop(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            try:
                self.poll_idle_once()
            except Exception:  # the monitor must never die and strand the model
                logger.exception("idle monitor tick failed for engine %s", self._backend.engine)

    def _default_poll(self) -> float:
        return max(MIN_POLL_SECONDS, min(MAX_POLL_SECONDS, self._idle_unload_seconds / 4.0))
