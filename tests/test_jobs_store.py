"""Tests for content_parser.jobs.store — CRUD with path-traversal guard."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_parser.core.errors import PluginError
from content_parser.jobs import store as store_module
from content_parser.jobs.schema import Job, SheetInput


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_jobs_"))
        # Override JOBS_DIR for the duration of each test.
        self._orig = store_module.JOBS_DIR
        store_module.JOBS_DIR = self.tmp

    def tearDown(self):
        store_module.JOBS_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _job(self, name="my-job"):
        return Job(name=name, source="vk", inputs={"community": ["durov_says"]})

    def test_save_and_load(self):
        path = store_module.save_job(self._job())
        self.assertTrue(path.exists())
        loaded = store_module.load_job("my-job")
        self.assertEqual(loaded.name, "my-job")
        self.assertEqual(loaded.inputs["community"], ["durov_says"])

    def test_save_sets_chmod_600(self):
        import os
        path = store_module.save_job(self._job())
        mode = os.stat(path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_list_jobs_returns_sorted(self):
        store_module.save_job(self._job("zebra"))
        store_module.save_job(self._job("alpha"))
        names = [j.name for j in store_module.list_jobs()]
        self.assertEqual(names, ["alpha", "zebra"])

    def test_list_jobs_skips_invalid_files(self):
        # Valid job
        store_module.save_job(self._job("good"))
        # Invalid file (won't parse as a Job)
        (self.tmp / "broken.yaml").write_text("not: a: valid: job", encoding="utf-8")
        names = [j.name for j in store_module.list_jobs()]
        self.assertEqual(names, ["good"])

    def test_list_invalid_returns_pairs(self):
        (self.tmp / "broken.yaml").write_text("source: \nimports:\n", encoding="utf-8")
        invalid = store_module.list_invalid()
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0][0], "broken")

    def test_load_missing_raises(self):
        with self.assertRaises(PluginError):
            store_module.load_job("nonexistent")

    def test_delete(self):
        store_module.save_job(self._job())
        self.assertTrue(store_module.delete_job("my-job"))
        self.assertFalse(store_module.delete_job("my-job"))

    def test_invalid_name_rejected(self):
        for bad in ("../etc", "name with spaces", "x" * 100, "foo/bar", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(PluginError):
                    store_module._job_path(bad)

    def test_job_exists(self):
        self.assertFalse(store_module.job_exists("nope"))
        store_module.save_job(self._job())
        self.assertTrue(store_module.job_exists("my-job"))


if __name__ == "__main__":
    unittest.main()
