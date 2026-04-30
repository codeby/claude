"""Tests for content_parser.transcription.cache — disk cache CRUD."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from content_parser.transcription import cache as cache_mod


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_cache_"))
        self._orig = cache_mod.CACHE_DIR
        cache_mod.CACHE_DIR = self.tmp

    def tearDown(self):
        cache_mod.CACHE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_missing_returns_none(self):
        self.assertIsNone(cache_mod.get("instagram", "AAA"))

    def test_put_then_get_round_trips(self):
        data = {
            "language": "ru", "is_generated": True,
            "segments": [{"start": 0.0, "duration": 2.0, "text": "hi"}],
            "text": "hi", "error": None,
        }
        cache_mod.put("instagram", "AAA", data)
        loaded = cache_mod.get("instagram", "AAA")
        self.assertEqual(loaded, data)

    def test_safe_filename_for_unsafe_id(self):
        data = {"language": "ru", "is_generated": True, "segments": [], "text": "x", "error": None}
        cache_mod.put("instagram", "../../etc/passwd", data)
        files = list(self.tmp.glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertNotIn("..", files[0].name)
        self.assertNotIn("/", files[0].name)

    def test_clear_removes_all(self):
        cache_mod.put("instagram", "a", {"text": "1"})
        cache_mod.put("vk", "b", {"text": "2"})
        n = cache_mod.clear()
        self.assertEqual(n, 2)
        self.assertIsNone(cache_mod.get("instagram", "a"))

    def test_atomic_write_no_tmp_left_after_success(self):
        cache_mod.put("instagram", "x", {"text": "ok"})
        # No .tmp sibling should remain
        tmps = list(self.tmp.glob("*.tmp"))
        self.assertEqual(tmps, [])

    def test_existing_value_replaced_atomically(self):
        cache_mod.put("instagram", "x", {"text": "first"})
        cache_mod.put("instagram", "x", {"text": "second"})
        loaded = cache_mod.get("instagram", "x")
        self.assertEqual(loaded["text"], "second")


if __name__ == "__main__":
    unittest.main()
