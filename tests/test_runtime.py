"""Runtime lifecycle: exactly-once load, serialization, recovery, idle eviction.

Everything here runs on the fake backend: no torch, no CUDA, no network.
"""
import threading
import time
import unittest

from exocore_tts.backends.fake import FakeBackend
from exocore_tts.errors import EngineUnavailable
from exocore_tts.runtime import ModelRuntime, RuntimeState
from exocore_tts.voices import VoiceAsset

PROBE_ASSET = VoiceAsset(key="probe", display_name="Probe")


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def synthesize_probe(backend):
    return lambda model: backend.synthesize(model, PROBE_ASSET, "并发。", None)


class RuntimeLifecycleTests(unittest.TestCase):
    def make_runtime(self, backend=None, **kwargs):
        backend = backend if backend is not None else FakeBackend()
        runtime = ModelRuntime(backend, **kwargs)
        self.addCleanup(runtime.close)
        return runtime, backend

    def test_cold_until_the_first_request_loads_then_stays_ready(self):
        runtime, backend = self.make_runtime()
        self.assertIs(runtime.state(), RuntimeState.COLD)
        self.assertEqual(runtime.run(lambda model: "done"), "done")
        self.assertIs(runtime.state(), RuntimeState.READY)
        self.assertEqual(backend.load_count, 1)
        runtime.run(lambda model: "again")
        self.assertEqual(backend.load_count, 1)

    def test_concurrent_first_requests_load_once_and_never_overlap(self):
        backend = FakeBackend(synth_delay_s=0.05)
        runtime, backend = self.make_runtime(backend)
        results: list[object] = []
        threads = [
            threading.Thread(target=lambda: results.append(runtime.run(synthesize_probe(backend))))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(backend.load_count, 1)
        self.assertEqual(backend.max_concurrent_synth, 1)
        self.assertEqual(backend.synth_count, 2)

    def test_many_concurrent_requests_never_overlap_and_load_once(self):
        backend = FakeBackend(synth_delay_s=0.01)
        runtime, backend = self.make_runtime(backend)
        results: list[object] = []
        threads = [
            threading.Thread(target=lambda: results.append(runtime.run(synthesize_probe(backend))))
            for _ in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(results), 5)
        self.assertEqual(backend.load_count, 1)
        self.assertEqual(backend.synth_count, 5)
        self.assertEqual(backend.max_concurrent_synth, 1)

    def test_health_reports_loading_without_waiting_for_the_worker(self):
        gate = threading.Event()
        backend = FakeBackend(load_gate=gate)
        runtime, _ = self.make_runtime(backend)
        failures: list[BaseException] = []

        def request():
            try:
                runtime.run(lambda model: "done")
            except BaseException as exc:  # pragma: no cover - only on a broken runtime
                failures.append(exc)

        worker = threading.Thread(target=request)
        worker.start()
        try:
            self.assertTrue(wait_for(lambda: runtime.state() is RuntimeState.LOADING))
            started = time.monotonic()
            self.assertIs(runtime.state(), RuntimeState.LOADING)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            gate.set()
            worker.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertIs(runtime.state(), RuntimeState.READY)

    def test_reentrant_run_from_the_worker_is_refused_instead_of_hanging(self):
        runtime, _ = self.make_runtime()

        def work(model):
            runtime.run(lambda inner: None)

        with self.assertRaises(RuntimeError):
            runtime.run(work)

    def test_load_failure_rolls_back_to_cold_and_the_next_request_retries(self):
        backend = FakeBackend(fail_load=True)
        runtime, backend = self.make_runtime(backend)
        with self.assertRaises(EngineUnavailable):
            runtime.run(lambda model: None)
        self.assertIs(runtime.state(), RuntimeState.COLD)
        self.assertEqual(backend.load_count, 1)
        backend.fail_load = False
        runtime.run(lambda model: "ok")
        self.assertIs(runtime.state(), RuntimeState.READY)
        self.assertEqual(backend.load_count, 2)

    def test_idle_eviction_releases_the_model_and_the_next_request_reloads(self):
        backend = FakeBackend()
        runtime, backend = self.make_runtime(
            backend, idle_unload_seconds=0.05, idle_poll_seconds=0.01
        )
        runtime.run(lambda model: "done")
        self.assertTrue(wait_for(lambda: runtime.state() is RuntimeState.COLD))
        self.assertEqual(backend.unload_count, 1)
        runtime.run(lambda model: "done")
        self.assertEqual(backend.load_count, 2)

    def test_idle_unload_zero_disables_eviction(self):
        runtime, backend = self.make_runtime()
        runtime.run(lambda model: "done")
        self.assertFalse(runtime.evict_if_idle())
        self.assertIs(runtime.state(), RuntimeState.READY)
        self.assertEqual(backend.unload_count, 0)

    def test_eviction_recheck_cancels_for_an_in_flight_request(self):
        clock = FakeClock()
        started = threading.Event()
        gate = threading.Event()
        backend = FakeBackend(synth_started=started, synth_gate=gate)
        runtime, backend = self.make_runtime(
            backend, idle_unload_seconds=5.0, idle_poll_seconds=1000.0, clock=clock
        )
        runtime.run(lambda model: "warm")

        failures: list[BaseException] = []

        def request():
            try:
                runtime.run(synthesize_probe(backend))
            except BaseException as exc:  # pragma: no cover - only on a broken runtime
                failures.append(exc)

        worker = threading.Thread(target=request)
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=3))
            clock.advance(60.0)  # the time check alone would call this idle now
            self.assertFalse(runtime.evict_if_idle())  # the in-flight request is the guard
            self.assertIs(runtime.state(), RuntimeState.READY)
        finally:
            gate.set()
            worker.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(backend.load_count, 1)  # the model was never evicted
        self.assertEqual(backend.unload_count, 0)

    def test_eviction_cannot_act_on_a_stale_timer_snapshot(self):
        clock = FakeClock()
        backend = FakeBackend()
        runtime, backend = self.make_runtime(
            backend, idle_unload_seconds=10.0, idle_poll_seconds=1000.0, clock=clock
        )
        runtime.run(lambda model: "warm")  # activity at t = 0
        clock.advance(60.0)  # an old snapshot would call this idle
        runtime.run(lambda model: "fresh")  # a fresh request refreshes activity
        clock.advance(1.0)
        self.assertFalse(runtime.evict_if_idle())
        self.assertIs(runtime.state(), RuntimeState.READY)
        self.assertEqual(backend.unload_count, 0)

    def test_monitor_never_evicts_while_a_request_is_in_flight(self):
        started = threading.Event()
        gate = threading.Event()
        backend = FakeBackend(synth_started=started, synth_gate=gate)
        runtime, backend = self.make_runtime(
            backend, idle_unload_seconds=0.01, idle_poll_seconds=0.005
        )
        runtime.run(lambda model: "warm")

        worker = threading.Thread(target=lambda: runtime.run(synthesize_probe(backend)))
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=3))
            time.sleep(0.1)  # ~20 monitor ticks, all of them must stand down
            self.assertIs(runtime.state(), RuntimeState.READY)
            self.assertEqual(backend.unload_count, 0)
        finally:
            gate.set()
            worker.join(timeout=5)

    def test_close_stops_the_monitor_and_releases_the_model(self):
        backend = FakeBackend()
        runtime, backend = self.make_runtime(
            backend, idle_unload_seconds=0.05, idle_poll_seconds=0.01
        )
        runtime.run(lambda model: "warm")
        runtime.close()
        self.assertIs(runtime.state(), RuntimeState.COLD)
        self.assertEqual(backend.unload_count, 1)
        runtime.close()  # idempotent
        self.assertEqual(backend.unload_count, 1)


if __name__ == "__main__":
    unittest.main()
