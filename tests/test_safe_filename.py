"""Tests for output._safe_filename and _file_stem path-traversal safety."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from content_parser.core.output import (
    _file_stem,
    _safe_filename,
    write_item_json,
    write_item_markdown,
)
from content_parser.core.schema import Item


class SafeFilenameTest(unittest.TestCase):
    def test_plain_text(self):
        self.assertEqual(_safe_filename("Some Title"), "Some_Title")

    def test_strips_path_separators(self):
        # forward slash is NOT \w, \s, or hyphen → removed
        self.assertEqual(_safe_filename("a/b/c"), "abc")

    def test_strips_dots_so_dotdot_cannot_escape(self):
        # The whole point: '..' resolves to nothing, no traversal possible
        self.assertEqual(_safe_filename("../../etc/passwd"), "etcpasswd")

    def test_strips_backslash(self):
        self.assertEqual(_safe_filename("a\\b\\c"), "abc")

    def test_strips_null_byte(self):
        self.assertEqual(_safe_filename("name\x00.txt"), "nametxt")

    def test_keeps_unicode_word_chars(self):
        # Russian text falls under \w with re.UNICODE
        self.assertIn("Привет", _safe_filename("Привет мир"))

    def test_truncates_to_max_length(self):
        out = _safe_filename("x" * 200, max_length=10)
        self.assertEqual(len(out), 10)

    def test_empty_falls_back(self):
        self.assertEqual(_safe_filename(""), "item")
        self.assertEqual(_safe_filename("///"), "item")


class FileStemCollisionTest(unittest.TestCase):
    """When item_id sanitizes to the fallback, a hash disambiguates."""

    def test_collision_when_ids_sanitize_to_same_fallback(self):
        a = Item(source="reddit", item_id="../../a", url="u", title="")
        b = Item(source="reddit", item_id="../../b", url="u", title="")
        self.assertNotEqual(_file_stem(a), _file_stem(b))

    def test_normal_id_unchanged_no_hash(self):
        item = Item(source="youtube", item_id="dQw4w9WgXcQ", url="u", title="x")
        stem = _file_stem(item)
        # stem should not contain the 'item-' fallback hash prefix
        self.assertNotIn("item-", stem)
        self.assertIn("dQw4w9WgXcQ", stem)

    def test_fallback_id_gets_stable_hash(self):
        item = Item(source="reddit", item_id="..", url="u", title="t")
        stem1 = _file_stem(item)
        stem2 = _file_stem(item)
        self.assertEqual(stem1, stem2)
        self.assertIn("item-", stem1)


class FileStemPathTraversalTest(unittest.TestCase):
    """Even if an upstream API returns malicious source/item_id, files stay in out_dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_traverse_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stem_strips_traversal_in_item_id(self):
        item = Item(
            source="reddit",
            item_id="../../etc/passwd",
            url="https://example",
            title="bad",
        )
        stem = _file_stem(item)
        self.assertNotIn("..", stem)
        self.assertNotIn("/", stem)
        self.assertNotIn("\\", stem)

    def test_stem_strips_traversal_in_source(self):
        item = Item(
            source="../../malicious",
            item_id="abc",
            url="https://example",
            title="ok",
        )
        stem = _file_stem(item)
        self.assertNotIn("..", stem)
        self.assertNotIn("/", stem)

    def test_write_item_json_stays_inside_out_dir(self):
        item = Item(
            source="reddit",
            item_id="../../escape",
            url="https://example",
            title="x",
        )
        path = write_item_json(item, self.tmp)
        # Resolved path must still be a child of out_dir
        self.assertTrue(
            path.resolve().is_relative_to(self.tmp.resolve()),
            f"Wrote to {path.resolve()} which escapes {self.tmp.resolve()}",
        )

    def test_write_item_markdown_stays_inside_out_dir(self):
        item = Item(
            source="../traverse",
            item_id="../escape",
            url="https://example",
            title="..",
        )
        path = write_item_markdown(item, self.tmp)
        self.assertTrue(path.resolve().is_relative_to(self.tmp.resolve()))


if __name__ == "__main__":
    unittest.main()
