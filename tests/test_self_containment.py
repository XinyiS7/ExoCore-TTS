"""Self-containment: the factory reads its own `.env`, never the sibling ExoCore checkout.

Plan/0005. No network, no GPU, no service: every case builds a throwaway layout under a
temporary directory and patches `config.repo_root` at it, so a real sibling checkout (or a
real key lying in one) can never influence the outcome.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from exocore_tts import config


class DotenvPathTest(unittest.TestCase):
    def test_default_is_this_repositorys_own_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ExoCore-TTS"
            root.mkdir()
            with mock.patch.object(config, "repo_root", return_value=root), mock.patch.dict(
                os.environ, {"EXOCORE_TTS_DOTENV": ""}
            ):
                resolved = config.dotenv_path()
        self.assertEqual(resolved, root / ".env")

    def test_default_does_not_point_into_the_sibling_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ExoCore-TTS"
            root.mkdir()
            with mock.patch.object(config, "repo_root", return_value=root), mock.patch.dict(
                os.environ, {"EXOCORE_TTS_DOTENV": ""}
            ):
                resolved = config.dotenv_path()
        self.assertNotEqual(resolved, root.parent / "ExoCore" / ".env")
        self.assertEqual(resolved.parent, root)
        self.assertEqual(resolved.name, ".env")

    def test_explicit_override_wins_and_is_used_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ExoCore-TTS"
            root.mkdir()
            explicit = root.parent / "shared" / "keys.env"
            with mock.patch.object(config, "repo_root", return_value=root), mock.patch.dict(
                os.environ, {"EXOCORE_TTS_DOTENV": str(explicit)}
            ):
                resolved = config.dotenv_path()
        self.assertEqual(resolved, explicit.resolve())


if __name__ == "__main__":
    unittest.main()
