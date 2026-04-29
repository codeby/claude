"""Tests for content_parser.jobs.runner — input merging + failure handling."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from content_parser.core.errors import PluginError
from content_parser.core.runner import RunResult
from content_parser.jobs import runner as runner_module
from content_parser.jobs.schema import Job, SheetInput


class ResolveInputsTest(unittest.TestCase):
    """`_resolve_inputs` merges inline values with values pulled from Sheets."""

    def test_inline_only(self):
        job = Job(
            name="x", source="vk",
            inputs={"community": ["a", "b"], "query": ["hello"]},
        )
        out = runner_module._resolve_inputs(job, secrets={})
        self.assertEqual(out, {"community": ["a", "b"], "query": ["hello"]})

    def test_sheets_only(self):
        job = Job(
            name="x", source="vk",
            sheet_inputs=[
                SheetInput(sheet="ID" * 12, target="community", range_a1="A:A"),
            ],
        )
        with patch("content_parser.loaders.gsheets.GoogleSheetsLoader") as MockLoader:
            mock_loader = MockLoader.from_secrets.return_value
            loaded = MagicMock()
            loaded.values = ["x", "y", "z"]
            mock_loader.load.return_value = loaded
            out = runner_module._resolve_inputs(
                job, secrets={"GOOGLE_SHEETS_CREDENTIALS": "fake"}
            )
        self.assertEqual(out, {"community": ["x", "y", "z"]})

    def test_inline_plus_sheets_dedupes(self):
        job = Job(
            name="x", source="vk",
            inputs={"community": ["dup", "inline-only"]},
            sheet_inputs=[
                SheetInput(sheet="ID" * 12, target="community"),
                SheetInput(sheet="ID" * 12, target="query", range_a1="B:B"),
            ],
        )
        with patch("content_parser.loaders.gsheets.GoogleSheetsLoader") as MockLoader:
            mock_loader = MockLoader.from_secrets.return_value

            def fake_load(sheet, **kwargs):
                if kwargs.get("range_a1") == "B:B":
                    return MagicMock(values=["search-term"])
                return MagicMock(values=["dup", "from-sheet"])

            mock_loader.load.side_effect = fake_load
            out = runner_module._resolve_inputs(
                job, secrets={"GOOGLE_SHEETS_CREDENTIALS": "fake"}
            )
        self.assertEqual(out["community"], ["dup", "inline-only", "from-sheet"])
        self.assertEqual(out["query"], ["search-term"])

    def test_drops_empty_kinds(self):
        # Inputs key with empty list shouldn't propagate.
        job = Job(
            name="x", source="vk",
            inputs={"community": ["a"], "query": []},
        )
        out = runner_module._resolve_inputs(job, secrets={})
        self.assertEqual(out, {"community": ["a"]})


class CollectSecretsTest(unittest.TestCase):
    def test_includes_plugin_keys(self):
        with patch("content_parser.jobs.runner.get_secret") as gs:
            gs.side_effect = lambda k: {"VK_ACCESS_TOKEN": "tok"}.get(k, "")
            secrets = runner_module._collect_secrets(["VK_ACCESS_TOKEN"], need_sheets=False)
        self.assertEqual(secrets["VK_ACCESS_TOKEN"], "tok")
        self.assertNotIn("GOOGLE_SHEETS_CREDENTIALS", secrets)

    def test_adds_sheets_when_needed(self):
        with patch("content_parser.jobs.runner.get_secret") as gs:
            gs.side_effect = lambda k: {
                "VK_ACCESS_TOKEN": "tok",
                "GOOGLE_SHEETS_CREDENTIALS": "creds",
            }.get(k, "")
            secrets = runner_module._collect_secrets(["VK_ACCESS_TOKEN"], need_sheets=True)
        self.assertEqual(secrets["GOOGLE_SHEETS_CREDENTIALS"], "creds")

    def test_picks_up_optional_proxy_secrets(self):
        with patch("content_parser.jobs.runner.get_secret") as gs:
            gs.side_effect = lambda k: {"WEBSHARE_USERNAME": "u", "WEBSHARE_PASSWORD": "p"}.get(k, "")
            secrets = runner_module._collect_secrets([], need_sheets=False)
        self.assertEqual(secrets["WEBSHARE_USERNAME"], "u")
        self.assertEqual(secrets["WEBSHARE_PASSWORD"], "p")


class RunJobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_runner_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _job(self, **kwargs):
        defaults = dict(
            name="test-job",
            source="vk",
            inputs={"community": ["durov_says"]},
            output_dir=str(self.tmp),
        )
        defaults.update(kwargs)
        return Job(**defaults)

    def test_calls_core_run_with_resolved_inputs(self):
        job = self._job()
        fake_plugin = MagicMock()
        fake_plugin.secret_keys = ["VK_ACCESS_TOKEN"]

        with patch("content_parser.jobs.runner.get_plugin", return_value=fake_plugin), \
             patch("content_parser.jobs.runner.get_secret", return_value="tok"), \
             patch("content_parser.jobs.runner.core_run") as mock_run:
            mock_run.return_value = RunResult(out_dir=self.tmp, items=[])
            result = runner_module.run_job_obj(job)

        self.assertIsInstance(result, RunResult)
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs["inputs"]["community"], ["durov_says"]) if "inputs" in kwargs else None
        # core_run was called as positional + kwargs
        args, kwargs = mock_run.call_args
        self.assertIs(args[0], fake_plugin)
        self.assertEqual(args[1], {"community": ["durov_says"]})
        self.assertEqual(args[2], {})

    def test_writes_last_run_marker_on_success(self):
        job = self._job()
        fake_plugin = MagicMock()
        fake_plugin.secret_keys = []

        with patch("content_parser.jobs.runner.get_plugin", return_value=fake_plugin), \
             patch("content_parser.jobs.runner.get_secret", return_value=""), \
             patch("content_parser.jobs.runner.core_run") as mock_run:
            mock_run.return_value = RunResult(out_dir=self.tmp / "fake", items=[])
            runner_module.run_job_obj(job)

        # _record_success writes into the runner's own resolved_output_dir, which
        # lives somewhere under self.tmp/<timestamp>/.
        markers = list(self.tmp.rglob(".last_run.txt"))
        self.assertEqual(len(markers), 1)
        self.assertIn("test-job", markers[0].read_text())

    def test_writes_last_error_on_failure(self):
        job = self._job()
        fake_plugin = MagicMock()
        fake_plugin.secret_keys = []

        with patch("content_parser.jobs.runner.get_plugin", return_value=fake_plugin), \
             patch("content_parser.jobs.runner.get_secret", return_value=""), \
             patch("content_parser.jobs.runner.core_run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                runner_module.run_job_obj(job)

        # The output dir was created as part of resolved_output_dir() resolution
        # in _record_failure. Find it under self.tmp.
        errors = list(self.tmp.rglob("last_error.txt"))
        self.assertEqual(len(errors), 1)
        text = errors[0].read_text()
        self.assertIn("boom", text)
        self.assertIn("RuntimeError", text)

    def test_notify_none_skips_error_marker(self):
        job = self._job(notify_on_failure="none")
        fake_plugin = MagicMock()
        fake_plugin.secret_keys = []

        with patch("content_parser.jobs.runner.get_plugin", return_value=fake_plugin), \
             patch("content_parser.jobs.runner.get_secret", return_value=""), \
             patch("content_parser.jobs.runner.core_run", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                runner_module.run_job_obj(job)

        errors = list(self.tmp.rglob("last_error.txt"))
        self.assertEqual(len(errors), 0)

    def test_empty_resolved_inputs_raises(self):
        # Both inline and sheets resolve to nothing → run_job refuses to call plugin.
        # We can't construct a Job with truly empty inputs (validation refuses),
        # but a sheet that returns nothing simulates the scenario.
        job = self._job(
            inputs={},
            sheet_inputs=[SheetInput(sheet="ID" * 12, target="community")],
        )
        fake_plugin = MagicMock()
        fake_plugin.secret_keys = []

        with patch("content_parser.jobs.runner.get_plugin", return_value=fake_plugin), \
             patch("content_parser.jobs.runner.get_secret", return_value="creds"), \
             patch("content_parser.loaders.gsheets.GoogleSheetsLoader") as MockLoader:
            MockLoader.from_secrets.return_value.load.return_value = MagicMock(values=[])

            with self.assertRaises(PluginError) as cm:
                runner_module.run_job_obj(job)
            self.assertIn("no resolved inputs", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
