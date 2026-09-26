"""Cloud path primitives: MIME parsing, WAV wrapping, key lookup, secret scrubbing.

No network, no GPU. `CloudVoiceClient` is only exercised on its guard clauses; the live
render path is covered by the smoke run recorded in the Plan.
"""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import soundfile as sf

from exocore_tts import cloud

FAKE_KEY = "AIzaSyFAKEKEYFORTESTING0123456789abcdef"


class MimeParsingTest(unittest.TestCase):
    def test_defaults_when_only_the_family_is_given(self):
        self.assertEqual(cloud.parse_audio_mime("audio/L16"), (16, 24000))

    def test_reads_rate_and_bits(self):
        self.assertEqual(cloud.parse_audio_mime("audio/L16;rate=44100"), (16, 44100))
        self.assertEqual(cloud.parse_audio_mime("audio/L24;rate=48000"), (24, 48000))

    def test_tolerates_empty_or_unparsable_parameters(self):
        self.assertEqual(cloud.parse_audio_mime("audio/L16;rate="), (16, 24000))
        self.assertEqual(cloud.parse_audio_mime("audio/L16;rate=abc;foo=bar"), (16, 24000))

    def test_pcm_detection(self):
        self.assertTrue(cloud.is_pcm_mime("audio/L16;rate=24000"))
        self.assertTrue(cloud.is_pcm_mime("audio/pcm"))
        self.assertFalse(cloud.is_pcm_mime("audio/wav"))


class WavWrappingTest(unittest.TestCase):
    def test_header_matches_the_declared_rate(self):
        pcm = b"\x00\x00" * 24000  # one second of 16-bit silence at 24 kHz
        data = cloud.wrap_pcm_in_wav(pcm, "audio/L16;rate=24000")
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WAVE")
        self.assertEqual(struct.unpack("<I", data[4:8])[0], 36 + len(pcm))
        channels = struct.unpack("<H", data[22:24])[0]
        rate = struct.unpack("<I", data[24:28])[0]
        bits = struct.unpack("<H", data[34:36])[0]
        self.assertEqual((channels, rate, bits), (1, 24000, 16))
        self.assertEqual(struct.unpack("<I", data[40:44])[0], len(pcm))
        self.assertAlmostEqual(cloud.wav_seconds(data), 1.0, places=3)

    def test_seconds_are_zero_for_an_unreadable_header(self):
        self.assertEqual(cloud.wav_seconds(b"oops"), 0.0)

    def test_result_is_readable_as_audio(self):
        pcm = b"\x00\x00" * 4800  # 0.2 s
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "clip.wav"
            target.write_bytes(cloud.wrap_pcm_in_wav(pcm, "audio/L16;rate=24000"))
            samples, rate = sf.read(target)
        self.assertEqual(rate, 24000)
        self.assertEqual(len(samples), 4800)


class ApiKeyTest(unittest.TestCase):
    def test_environment_wins(self):
        with mock.patch.dict(os.environ, {cloud.DEFAULT_ENV_VAR: FAKE_KEY}):
            self.assertEqual(cloud.read_api_key(Path("does-not-exist.env")), FAKE_KEY)

    def test_falls_back_to_the_env_file_and_strips_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text(f'OTHER=1\n{cloud.DEFAULT_ENV_VAR}="{FAKE_KEY}"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {cloud.DEFAULT_ENV_VAR: ""}):
                self.assertEqual(cloud.read_api_key(dotenv), FAKE_KEY)

    def test_missing_key_raises_without_leaking_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.env"
            with mock.patch.dict(os.environ, {cloud.DEFAULT_ENV_VAR: ""}):
                with self.assertRaises(cloud.CloudError) as caught:
                    cloud.read_api_key(missing)
        self.assertIn(cloud.DEFAULT_ENV_VAR, str(caught.exception))


class SecretScrubbingTest(unittest.TestCase):
    def test_removes_the_key_and_the_fragments_sdks_quote_back(self):
        message = f"400 INVALID_ARGUMENT: key {FAKE_KEY} rejected (prefix {FAKE_KEY[:12]})"
        cleaned = cloud.scrub_secrets(message, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, cleaned)
        self.assertNotIn(FAKE_KEY[:12], cleaned)
        self.assertNotIn(FAKE_KEY[-8:], cleaned)
        self.assertIn("***", cleaned)


class ClientGuardTest(unittest.TestCase):
    def test_render_without_a_voice_fails_before_any_request(self):
        client = cloud.CloudVoiceClient(FAKE_KEY)
        with self.assertRaises(cloud.CloudError) as caught:
            client.render("你好")
        self.assertIn("voice", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
