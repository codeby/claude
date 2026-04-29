"""Tests for content_parser.core.runner — partial-run safety."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Iterator

from content_parser.core.plugin import InputSpec, SourcePlugin
from content_parser.core.runner import run
from content_parser.core.schema import Item


class _CrashAfterTwo(SourcePlugin):
    name = "crash"
    label = "Crash"
    secret_keys = []

    def input_specs(self): return [InputSpec(kind="x", label="X")]
    def settings_specs(self): return []
    def resolve(self, *a, **k): return ["a", "b", "c"]

    def fetch(self, ids, *a, **k) -> Iterator[Item]:
        yield Item(source="crash", item_id="a", url="u/a", title="A")
        yield Item(source="crash", item_id="b", url="u/b", title="B")
        raise RuntimeError("boom on third")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_run_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partial_run_writes_summary_and_index_then_reraises(self):
        plugin = _CrashAfterTwo()
        with self.assertRaises(RuntimeError) as cm:
            run(plugin, {"x": ["1"]}, {}, {}, output_dir=self.tmp, log=lambda _: None)
        self.assertIn("boom", str(cm.exception))

        files = {p.name for p in self.tmp.iterdir()}
        self.assertIn("summary.csv", files)
        self.assertIn("index.md", files)

        summary = (self.tmp / "summary.csv").read_text(encoding="utf-8")
        # Both successful items should be in summary
        self.assertIn(",a,A,", summary)
        self.assertIn(",b,B,", summary)


if __name__ == "__main__":
    unittest.main()
