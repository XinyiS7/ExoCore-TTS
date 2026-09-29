"""Self-containment: the factory reads its own `.env`, never the sibling ExoCore checkout.

Plan/0005. No network, no GPU, no service: every case builds a throwaway layout under a
temporary directory and patches `config.repo_root` at it, so a real sibling checkout (or a
real key lying in one) can never influence the outcome.
"""
import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from exocore_tts import cloud, config

SIBLING_KEY = "AIzaSyPLAUSIBLESIBLINGKEY0123456789ab"
OWN_KEY = "AIzaSyOWNKEYFORTESTING0123456789abcdef"
OTHER_KEY = "AIzaSyOTHERPROJECTKEY0123456789abcd"


@contextlib.contextmanager
def _isolated_factory(
    tmp: str,
    *,
    own_env: str | None = None,
    sibling_env: str | None = None,
    extra_env: dict[str, str] | None = None,
):
    """Point the factory at `tmp/ExoCore-TTS`, with a sibling `tmp/ExoCore` checkout next to it.

    Both key variables are cleared first, so the host machine's own variables or `.env`
    cannot leak into a case; `extra_env` then adds what a case needs.
    """
    root = Path(tmp) / "ExoCore-TTS"
    root.mkdir()
    sibling = Path(tmp) / "ExoCore"
    sibling.mkdir()
    if own_env is not None:
        (root / ".env").write_text(own_env, encoding="utf-8")
    if sibling_env is not None:
        (sibling / ".env").write_text(sibling_env, encoding="utf-8")
    env = {"GEMINI_API_KEY": "", "EXOCORE_TTS_DOTENV": ""}
    env.update(extra_env or {})
    with mock.patch.object(config, "repo_root", return_value=root), mock.patch.dict(
        os.environ, env
    ):
        yield root


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


class SiblingCheckoutIsNeverReadTest(unittest.TestCase):
    """Headline regression (Plan/0005 §4): a perfectly plausible key sitting in the sibling
    `ExoCore/.env` must not be picked up -- not even when this repository has no `.env`."""

    def test_plausible_sibling_key_is_ignored_when_our_own_env_is_missing(self):
        sibling_env = f"GEMINI_API_KEY={SIBLING_KEY}\nGEM_TTS_KEY={SIBLING_KEY}\n"
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, sibling_env=sibling_env
        ):
            with self.assertRaises(cloud.CloudError) as caught:
                cloud.read_api_key()
        message = str(caught.exception)
        self.assertNotIn(SIBLING_KEY, message)
        self.assertNotIn(SIBLING_KEY[:12], message)
        self.assertIn(".env", message)

    def test_our_own_env_is_the_only_implicit_source(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env=f"GEM_TTS_KEY={OWN_KEY}\n", sibling_env=f"GEMINI_API_KEY={SIBLING_KEY}\n"
        ) as root:
            self.assertEqual(config.dotenv_path(), root / ".env")
            self.assertEqual(cloud.read_api_key(), OWN_KEY)

    def test_sibling_key_is_not_used_even_as_a_later_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env="OTHER=1\n", sibling_env=f"GEM_TTS_KEY={SIBLING_KEY}\n"
        ):
            with self.assertRaises(cloud.CloudError):
                cloud.read_api_key()


class KeyPrecedenceTest(unittest.TestCase):
    def test_process_environment_beats_the_key_file(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env=f"GEM_TTS_KEY={OTHER_KEY}\n", extra_env={"GEMINI_API_KEY": OWN_KEY}
        ):
            self.assertEqual(cloud.read_api_key(), OWN_KEY)

    def test_canonical_name_beats_the_legacy_name_in_one_file(self):
        both = f"GEMINI_API_KEY={OTHER_KEY}\nGEM_TTS_KEY={OWN_KEY}\n"
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(tmp, own_env=both):
            self.assertEqual(cloud.read_api_key(), OWN_KEY)

    def test_legacy_name_still_works_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env=f"GEMINI_API_KEY={OWN_KEY}\n"
        ):
            self.assertEqual(cloud.read_api_key(), OWN_KEY)

    def test_explicit_argument_beats_the_environment_override(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env=f"GEM_TTS_KEY={OTHER_KEY}\n"
        ) as root:
            chosen = root.parent / "chosen.env"
            chosen.write_text(f"GEM_TTS_KEY={OWN_KEY}\n", encoding="utf-8")
            override = root.parent / "override.env"
            override.write_text(f"GEM_TTS_KEY={OTHER_KEY}\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"EXOCORE_TTS_DOTENV": str(override)}):
                self.assertEqual(cloud.read_api_key(chosen), OWN_KEY)

    def test_environment_override_is_explicit_only_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp,
            own_env=f"GEM_TTS_KEY={OWN_KEY}\n",
            extra_env={"EXOCORE_TTS_DOTENV": str(Path(tmp) / "absent.env")},
        ):
            with self.assertRaises(cloud.CloudError) as caught:
                cloud.read_api_key()
        self.assertNotIn(OWN_KEY, str(caught.exception))

    def test_environment_override_file_is_used_when_present(self):
        with tempfile.TemporaryDirectory() as tmp, _isolated_factory(
            tmp, own_env=f"GEM_TTS_KEY={OTHER_KEY}\n"
        ) as root:
            shared = root.parent / "shared.env"
            shared.write_text(f"GEM_TTS_KEY={OWN_KEY}\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"EXOCORE_TTS_DOTENV": str(shared)}):
                self.assertEqual(cloud.read_api_key(), OWN_KEY)


class DotenvParsingTest(unittest.TestCase):
    """The file grammar is literal and bounded: anything unclear reads as unset."""

    def value_of(self, text: str) -> str | None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text, encoding="utf-8")
            return cloud.dotenv_value(path)

    def test_missing_file_is_none_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cloud.dotenv_value(Path(tmp) / "absent.env"))

    def test_crlf_lines_are_tolerated(self):
        self.assertEqual(self.value_of(f"GEM_TTS_KEY={OWN_KEY}\r\n"), OWN_KEY)
        self.assertEqual(self.value_of(f'GEM_TTS_KEY="{OWN_KEY}"\r\n'), OWN_KEY)

    def test_matching_quotes_and_inner_whitespace_are_stripped(self):
        self.assertEqual(self.value_of(f'GEM_TTS_KEY="  {OWN_KEY}  "\n'), OWN_KEY)
        self.assertEqual(self.value_of(f"GEM_TTS_KEY='  {OWN_KEY}  '\n"), OWN_KEY)
        self.assertEqual(self.value_of(f"GEM_TTS_KEY =  {OWN_KEY}  \n"), OWN_KEY)

    def test_empty_values_read_as_unset(self):
        self.assertIsNone(self.value_of("GEM_TTS_KEY=\n"))
        self.assertIsNone(self.value_of('GEM_TTS_KEY=""\n'))
        self.assertIsNone(self.value_of("GEM_TTS_KEY=''\r\n"))

    def test_comments_unrelated_lines_and_near_miss_names_are_ignored(self):
        text = f"# GEM_TTS_KEY=nope\nOTHER=1\nGEM_TTS_KEY_OLD=zzz\nMYGEM_TTS_KEY=zzz\n\n"
        self.assertIsNone(self.value_of(text))

    def test_first_non_empty_value_wins(self):
        self.assertEqual(self.value_of(f"GEM_TTS_KEY=\nGEM_TTS_KEY={OWN_KEY}\n"), OWN_KEY)
        self.assertEqual(
            self.value_of(f"GEM_TTS_KEY={OWN_KEY}\nGEM_TTS_KEY={OTHER_KEY}\n"), OWN_KEY
        )

    def test_value_may_contain_equals_signs(self):
        self.assertEqual(self.value_of('GEM_TTS_KEY="a=b=c"\n'), "a=b=c")

    def test_names_order_is_respected_not_line_order(self):
        text = f"GEMINI_API_KEY={OTHER_KEY}\nGEM_TTS_KEY={OWN_KEY}\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(cloud.dotenv_value(path), OWN_KEY)


if __name__ == "__main__":
    unittest.main()
