"""HTTP contract: the three accepted fields, bearer auth, error bodies, health, lifecycle."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import soundfile as sf
from fastapi.testclient import TestClient

from exocore_tts import server, voices
from exocore_tts.backends.fake import FakeBackend
from exocore_tts.config import ConfigError, DaemonConfig, load_daemon_config
from exocore_tts.server import create_app
from exocore_tts.service import TtsService


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._previous_voice_root = os.environ.get("EXOCORE_TTS_VOICE_ROOT")
        os.environ["EXOCORE_TTS_VOICE_ROOT"] = str(self.root)
        self.addCleanup(self._restore_environment)
        self.voice = self.add_voice()

    def _restore_environment(self):
        if self._previous_voice_root is None:
            os.environ.pop("EXOCORE_TTS_VOICE_ROOT", None)
        else:
            os.environ["EXOCORE_TTS_VOICE_ROOT"] = self._previous_voice_root
        self._tmp.cleanup()

    def add_voice(self, key="probe", engine="fake"):
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"key": key, "display_name": key, "engine": engine, "prompt_text": "参考句。"}
        (directory / voices.VOICE_MANIFEST).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return key

    def make_client(self, backend=None, *, token="", max_text_chars=600, registry=None):
        backend = backend if backend is not None else FakeBackend()
        registry = registry if registry is not None else {"fake": backend}
        service = TtsService(registry, max_text_chars=max_text_chars, idle_unload_seconds=0.0)
        self.addCleanup(service.close)
        config = DaemonConfig(
            host="127.0.0.1",
            port=8769,
            token=token,
            idle_unload_seconds=0,
            max_text_chars=max_text_chars,
        )
        client = TestClient(create_app(config, service), raise_server_exceptions=False)
        self.addCleanup(client.close)
        return client, backend, service

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(response.json(), {"error": code})  # exactly one field, always


class HealthContractTests(ServerTestCase):
    def test_health_shape_is_exactly_status_and_state(self):
        client, _, _ = self.make_client()
        with client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "state": "cold"})

    def test_health_answers_loading_while_a_cold_request_waits(self):
        gate = threading.Event()
        client, backend, _ = self.make_client(FakeBackend(load_gate=gate))
        responses = []
        with client:
            worker = threading.Thread(
                target=lambda: responses.append(
                    client.post("/tts", json={"text": "你好。", "voice_key": self.voice})
                )
            )
            worker.start()
            try:
                self.assertTrue(
                    wait_for(lambda: client.get("/health").json()["state"] == "loading")
                )
                started = time.monotonic()
                self.assertEqual(client.get("/health").json()["state"], "loading")
                self.assertLess(time.monotonic() - started, 0.5)  # never waits for the worker
            finally:
                gate.set()
                worker.join(timeout=5)
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(backend.load_count, 1)


class TtsContractTests(ServerTestCase):
    def test_success_returns_readable_wav_bytes(self):
        client, _, _ = self.make_client()
        with client:
            response = client.post("/tts", json={"text": "你好。", "voice_key": self.voice})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        samples, rate = sf.read(io.BytesIO(response.content))
        self.assertGreater(len(samples), 0)
        self.assertEqual(rate, 24000)

    def test_unknown_fields_and_old_knobs_are_refused(self):
        client, _, _ = self.make_client()
        with client:
            for payload in (
                {"text": "你好。", "voice_key": self.voice, "style": "低沉"},
                {"text": "你好。", "voice_key": self.voice, "seed": 1},
                {"text": "你好。", "voice_key": self.voice, "defaults": {"cfg_value": 2}},
                {"text": "你好。", "voice_key": self.voice, "verify": True},
                {"text": "你好。", "voice_key": self.voice, "format": "wav"},
                {"text": "你好。", "voice_key": self.voice, "engine": "voxcpm2"},
            ):
                self.assert_error(client.post("/tts", json=payload), 422, "invalid_request")

    def test_request_field_types_and_bounds(self):
        client, _, _ = self.make_client(max_text_chars=10)
        with client:
            cases = [
                ({"voice_key": self.voice}, 422, "invalid_request"),
                ({"text": 5, "voice_key": self.voice}, 422, "invalid_request"),
                ({"text": "   \n ", "voice_key": self.voice}, 422, "invalid_request"),
                ({"text": "字" * 11, "voice_key": self.voice}, 422, "invalid_request"),
                ({"text": "一。", "voice_key": self.voice, "delivery": None}, 422, "invalid_request"),
                (
                    {"text": "一。", "voice_key": self.voice, "delivery": "d" * 501},
                    422,
                    "invalid_request",
                ),
                ({"text": "一。", "voice_key": "Probe V1"}, 422, "invalid_request"),
                ({"text": "一。", "voice_key": "never_made"}, 404, "unknown_voice"),
            ]
            for payload, status, code in cases:
                self.assert_error(client.post("/tts", json=payload), status, code)
            # boundaries that must succeed: text at the limit, delivery at the limit
            self.assertEqual(
                client.post("/tts", json={"text": "字" * 10, "voice_key": self.voice}).status_code,
                200,
            )
            self.assertEqual(
                client.post(
                    "/tts",
                    json={"text": "一。", "voice_key": self.voice, "delivery": "d" * 500},
                ).status_code,
                200,
            )

    def test_malformed_json_keeps_the_error_shape(self):
        client, _, _ = self.make_client()
        with client:
            response = client.post(
                "/tts", content="{not json", headers={"content-type": "application/json"}
            )
            self.assert_error(response, 422, "invalid_request")
            response = client.post(
                "/tts", content="text=hello", headers={"content-type": "text/plain"}
            )
            self.assert_error(response, 422, "invalid_request")

    def test_delivery_unsupported_is_explicit_not_ignored(self):
        client, backend, _ = self.make_client(FakeBackend(delivery_supported=False))
        with client:
            response = client.post(
                "/tts", json={"text": "一。", "voice_key": self.voice, "delivery": "耳语"}
            )
        self.assert_error(response, 422, "delivery_unsupported")
        self.assertEqual(backend.synth_count, 0)

    def test_engine_unavailable_when_no_backend_serves_the_engine(self):
        client, _, _ = self.make_client(registry={})
        with client:
            response = client.post("/tts", json={"text": "一。", "voice_key": self.voice})
        self.assert_error(response, 503, "engine_unavailable")

    def test_synthesis_failure_maps_to_500(self):
        client, _, _ = self.make_client(FakeBackend(fail_on_text="你"))
        with client:
            response = client.post("/tts", json={"text": "你好。", "voice_key": self.voice})
        self.assert_error(response, 500, "synthesis_failed")

    def test_load_failure_is_recoverable_over_http(self):
        backend = FakeBackend(fail_load=True)
        client, backend, _ = self.make_client(backend)
        with client:
            body = {"text": "一。", "voice_key": self.voice}
            self.assert_error(client.post("/tts", json=body), 503, "engine_unavailable")
            self.assertEqual(client.get("/health").json()["state"], "cold")
            backend.fail_load = False
            self.assertEqual(client.post("/tts", json=body).status_code, 200)
        self.assertEqual(backend.load_count, 2)

    def test_no_diagnostics_leak_through_headers_or_bodies(self):
        client, _, _ = self.make_client()
        with client:
            response = client.post("/tts", json={"text": "你好。", "voice_key": self.voice})
            health = client.get("/health").json()
        header_names = [name.lower() for name in response.headers]
        for forbidden in ("engine", "device", "rtf", "segment", "queue", "voxcpm"):
            self.assertFalse(any(forbidden in name for name in header_names), forbidden)
        self.assertEqual(set(health), {"status", "state"})


class AuthTests(ServerTestCase):
    def test_without_a_token_the_port_is_open_on_loopback(self):
        client, _, _ = self.make_client()
        with client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(
                client.post("/tts", json={"text": "一。", "voice_key": self.voice}).status_code, 200
            )

    def test_with_a_token_both_endpoints_require_the_bearer(self):
        client, _, _ = self.make_client(token="s3cret")
        with client:
            self.assert_error(client.get("/health"), 401, "unauthorized")
            self.assert_error(
                client.get("/health", headers={"Authorization": "Bearer wrong"}),
                401,
                "unauthorized",
            )
            self.assert_error(
                client.get("/health", headers={"Authorization": "s3cret"}), 401, "unauthorized"
            )
            self.assert_error(
                client.post("/tts", json={"text": "一。", "voice_key": self.voice}),
                401,
                "unauthorized",
            )
            # auth is checked before the body: blank text still answers 401
            self.assert_error(client.post("/tts", json={"text": ""}), 401, "unauthorized")
            self.assertEqual(
                client.get("/health", headers={"Authorization": "Bearer s3cret"}).status_code, 200
            )
            self.assertEqual(
                client.post(
                    "/tts",
                    json={"text": "一。", "voice_key": self.voice},
                    headers={"Authorization": "Bearer s3cret"},
                ).status_code,
                200,
            )


class ConcurrencyTests(ServerTestCase):
    def test_two_cold_requests_load_once_and_both_succeed(self):
        client, backend, _ = self.make_client(FakeBackend(synth_delay_s=0.05))
        responses = []
        with client:
            threads = [
                threading.Thread(
                    target=lambda: responses.append(
                        client.post("/tts", json={"text": "并发。", "voice_key": self.voice})
                    )
                )
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(backend.load_count, 1)
        self.assertEqual(backend.max_concurrent_synth, 1)


class LifecycleTests(ServerTestCase):
    def test_app_shutdown_closes_the_engine(self):
        client, backend, service = self.make_client()
        with client:
            client.post("/tts", json={"text": "你好。", "voice_key": self.voice})
            self.assertEqual(service.health_state(), "ready")
        self.assertEqual(service.health_state(), "cold")
        self.assertEqual(backend.unload_count, 1)


class OutOfContractPathsTests(ServerTestCase):
    def test_unknown_routes_and_methods_keep_the_single_field_shape(self):
        client, _, _ = self.make_client()
        with client:
            self.assert_error(client.get("/voices"), 404, "invalid_request")
            self.assert_error(client.get("/openapi.json"), 404, "invalid_request")
            self.assert_error(client.get("/tts"), 405, "invalid_request")


class ConfigGateTests(unittest.TestCase):
    def test_defaults(self):
        config = load_daemon_config({})
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8769)
        self.assertEqual(config.token, "")
        self.assertEqual(config.idle_unload_seconds, 1800)
        self.assertEqual(config.max_text_chars, 600)

    def test_literal_loopback_hosts_are_accepted(self):
        for host in ("127.0.0.1", "::1", "127.0.0.53"):
            self.assertEqual(load_daemon_config({"EXOCORE_TTS_HOST": host}).host, host)

    def test_non_loopback_hosts_are_refused_even_with_a_token(self):
        hosts = ("0.0.0.0", "::", "localhost", "192.168.1.10", "example.com", "127.0.0.1.nip.io")
        for host in hosts:
            with self.assertRaises(ConfigError):
                load_daemon_config({"EXOCORE_TTS_HOST": host, "EXOCORE_TTS_TOKEN": "s3cret"})

    def test_bad_numbers_are_refused(self):
        for env in (
            {"EXOCORE_TTS_PORT": "abc"},
            {"EXOCORE_TTS_PORT": "0"},
            {"EXOCORE_TTS_PORT": "65536"},
            {"EXOCORE_TTS_IDLE_UNLOAD_SECONDS": "-1"},
            {"EXOCORE_TTS_MAX_TEXT_CHARS": "0"},
        ):
            with self.assertRaises(ConfigError):
                load_daemon_config(env)

    def test_zero_idle_disables_unloading_and_a_blank_token_is_no_token(self):
        config = load_daemon_config(
            {"EXOCORE_TTS_IDLE_UNLOAD_SECONDS": "0", "EXOCORE_TTS_TOKEN": "   "}
        )
        self.assertEqual(config.idle_unload_seconds, 0)
        self.assertEqual(config.token, "")

    def test_entry_point_refuses_a_non_loopback_host(self):
        with mock.patch.dict(os.environ, {"EXOCORE_TTS_HOST": "0.0.0.0"}, clear=False):
            self.assertEqual(server.main(), 2)


if __name__ == "__main__":
    unittest.main()
