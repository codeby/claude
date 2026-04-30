"""Tests for content_parser.core.secrets — TOML upsert/escape/remove."""
from __future__ import annotations

import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path

from content_parser.core import secrets as s


class TomlUpsertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_sec_"))
        self._orig_secrets = s.SECRETS_PATH
        s.SECRETS_PATH = self.tmp / ".streamlit" / "secrets.toml"

    def tearDown(self):
        s.SECRETS_PATH = self._orig_secrets
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_escape_quote_and_backslash(self):
        self.assertEqual(s._toml_escape('plain'), 'plain')
        self.assertEqual(s._toml_escape('with"quote'), 'with\\"quote')
        self.assertEqual(s._toml_escape('with\\backslash'), 'with\\\\backslash')

    def test_fresh_write_is_valid_toml(self):
        s._upsert_secrets_toml("KEY", "value")
        parsed = tomllib.loads(s.SECRETS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(parsed["KEY"], "value")

    def test_value_with_quotes_round_trips(self):
        tricky = 'a"b\\c'
        s._upsert_secrets_toml("TRICKY", tricky)
        parsed = tomllib.loads(s.SECRETS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(parsed["TRICKY"], tricky)

    def test_replace_preserves_other_keys(self):
        s.SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        s.SECRETS_PATH.write_text(
            'OTHER = "keep"\nKEY = "old"\nMORE = "also"\n', encoding="utf-8"
        )
        s._upsert_secrets_toml("KEY", "new")
        text = s.SECRETS_PATH.read_text(encoding="utf-8")
        self.assertIn("OTHER", text)
        self.assertIn("MORE", text)
        self.assertIn('KEY = "new"', text)
        self.assertNotIn('"old"', text)

    def test_remove_preserves_others(self):
        s.SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        s.SECRETS_PATH.write_text(
            'OTHER = "keep"\nKEY = "x"\n', encoding="utf-8"
        )
        s._remove_from_secrets_toml("KEY")
        text = s.SECRETS_PATH.read_text(encoding="utf-8")
        self.assertNotIn("KEY", text)
        self.assertIn("OTHER", text)

    def test_remove_sole_key_deletes_file(self):
        s.SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        s.SECRETS_PATH.write_text('KEY = "x"\n', encoding="utf-8")
        s._remove_from_secrets_toml("KEY")
        self.assertFalse(s.SECRETS_PATH.exists())

    def test_streamlit_cloud_skip_writes(self):
        from unittest.mock import patch
        # On Streamlit Cloud, secrets.toml is read-only; we must NOT touch it.
        with patch.dict("os.environ", {"STREAMLIT_RUNTIME": "cloud"}):
            self.assertTrue(s._is_streamlit_cloud())
            s._upsert_secrets_toml("KEY", "value")
            # File should not have been created
            self.assertFalse(s.SECRETS_PATH.exists())

    def test_local_environment_writes_normally(self):
        from unittest.mock import patch
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(s._is_streamlit_cloud())
            s._upsert_secrets_toml("KEY", "value")
            self.assertTrue(s.SECRETS_PATH.exists())


if __name__ == "__main__":
    unittest.main()
