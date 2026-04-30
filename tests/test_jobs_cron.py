"""Tests for content_parser.jobs.cron — managed crontab block I/O."""
from __future__ import annotations

import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

from content_parser.jobs import cron as cron_module
from content_parser.jobs.cron import (
    BEGIN_MARKER,
    END_MARKER,
    CronError,
    _build_block,
    _strip_block,
    build_command_for_job,
)
from content_parser.jobs.schema import Job


class StripBlockTest(unittest.TestCase):
    def test_no_block_returns_unchanged(self):
        text = "0 0 * * * date\n5 * * * * uptime\n"
        self.assertEqual(_strip_block(text).rstrip(), text.rstrip())

    def test_strips_block_only(self):
        text = (
            "0 0 * * * date\n"
            f"{BEGIN_MARKER}\n"
            "0 6 * * MON cd /x && python -m content_parser.cli jobs run foo\n"
            f"{END_MARKER}\n"
            "5 * * * * uptime\n"
        )
        result = _strip_block(text)
        self.assertNotIn(BEGIN_MARKER, result)
        self.assertNotIn("python -m content_parser", result)
        self.assertIn("0 0 * * * date", result)
        self.assertIn("5 * * * * uptime", result)

    def test_handles_block_at_start(self):
        text = (
            f"{BEGIN_MARKER}\n"
            "5 * * * * managed\n"
            f"{END_MARKER}\n"
            "5 * * * * outside\n"
        )
        result = _strip_block(text)
        self.assertNotIn("managed", result)
        self.assertIn("outside", result)


class BuildBlockTest(unittest.TestCase):
    def test_empty_jobs_no_block(self):
        self.assertEqual(_build_block([]), [])

    def test_block_has_markers(self):
        from content_parser.jobs.cron import CronEntry
        lines = _build_block([
            CronEntry(schedule="0 6 * * MON", job_name="foo", command="cd / && true"),
        ])
        self.assertEqual(lines[0], BEGIN_MARKER)
        self.assertEqual(lines[-1], END_MARKER)
        self.assertIn("0 6 * * MON", lines[1])
        self.assertIn("# job:foo", lines[1])


class BuildCommandTest(unittest.TestCase):
    def test_command_quotes_paths(self):
        job = Job(name="my-job", source="vk", inputs={"community": ["x"]})
        cmd = build_command_for_job(
            job,
            project_root=Path("/path with spaces/repo"),
            python_executable="/usr/bin/python3",
            log_path=Path("/var/log with spaces/cron.log"),
        )
        # Paths with spaces must be shell-quoted; safe paths can stay as-is.
        self.assertIn(shlex.quote("/path with spaces/repo"), cmd)
        self.assertIn(shlex.quote("/var/log with spaces/cron.log"), cmd)
        self.assertIn("jobs run my-job", cmd)
        self.assertIn("2>&1", cmd)
        self.assertTrue(cmd.startswith("cd "))

    def test_default_log_path_is_under_project_root(self):
        job = Job(name="x", source="vk", inputs={"community": ["a"]})
        cmd = build_command_for_job(
            job, project_root=Path("/repo"), python_executable="/usr/bin/python3"
        )
        self.assertIn("/repo/output/scheduled/.cron.log", cmd)

    def test_newline_in_project_root_rejected(self):
        # crontab is line-based; a literal newline inside any path would split
        # the entry across lines and corrupt the file. shlex.quote does NOT
        # protect against this — it just wraps the bytes in single quotes.
        job = Job(name="x", source="vk", inputs={"community": ["a"]})
        with self.assertRaises(CronError) as cm:
            build_command_for_job(
                job,
                project_root=Path("/path\nnewline/repo"),
                python_executable="/usr/bin/python3",
            )
        self.assertIn("newline", str(cm.exception).lower())

    def test_newline_in_python_executable_rejected(self):
        job = Job(name="x", source="vk", inputs={"community": ["a"]})
        with self.assertRaises(CronError):
            build_command_for_job(
                job,
                project_root=Path("/repo"),
                python_executable="/usr/bin/py\nthon",
            )

    def test_newline_in_log_path_rejected(self):
        job = Job(name="x", source="vk", inputs={"community": ["a"]})
        with self.assertRaises(CronError):
            build_command_for_job(
                job,
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
                log_path=Path("/var/log\nbreak/cron.log"),
            )

    def test_carriage_return_also_rejected(self):
        job = Job(name="x", source="vk", inputs={"community": ["a"]})
        with self.assertRaises(CronError):
            build_command_for_job(
                job,
                project_root=Path("/repo\r/here"),
                python_executable="/usr/bin/python3",
            )


class InstallCronTest(unittest.TestCase):
    """install_cron orchestrates crontab -l → strip → write."""

    def _patches(self, current_crontab: str = ""):
        return patch.multiple(
            "content_parser.jobs.cron",
            _existing_crontab=lambda: current_crontab,
            _write_crontab=patch.DEFAULT,
        )

    def _job(self, name="weekly", schedule="0 6 * * MON"):
        return Job(
            name=name, source="vk",
            inputs={"community": ["a"]},
            schedule=schedule,
        )

    def test_first_install_appends_block(self):
        written: dict = {}
        with patch("content_parser.jobs.cron._existing_crontab", return_value="0 0 * * * date\n"), \
             patch("content_parser.jobs.cron._write_crontab", side_effect=lambda t: written.setdefault("text", t)):
            entries = cron_module.install_cron(
                jobs=[self._job()],
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
            )
        self.assertEqual(len(entries), 1)
        text = written["text"]
        self.assertIn("0 0 * * * date", text)
        self.assertIn(BEGIN_MARKER, text)
        self.assertIn(END_MARKER, text)
        self.assertIn("# job:weekly", text)

    def test_idempotent_replace(self):
        # Run install twice — the second run should produce the same result.
        existing_after_first = ""
        written: list[str] = []

        def write(t):
            written.append(t)
            nonlocal existing_after_first
            existing_after_first = t

        def existing():
            return existing_after_first

        with patch("content_parser.jobs.cron._existing_crontab", side_effect=existing), \
             patch("content_parser.jobs.cron._write_crontab", side_effect=write):
            cron_module.install_cron(
                jobs=[self._job()],
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
            )
            cron_module.install_cron(
                jobs=[self._job()],
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
            )

        self.assertEqual(written[0], written[1])

    def test_jobs_without_schedule_are_skipped(self):
        manual_only = Job(
            name="manual-only", source="vk",
            inputs={"community": ["a"]},
        )  # no schedule

        written: dict = {}
        with patch("content_parser.jobs.cron._existing_crontab", return_value=""), \
             patch("content_parser.jobs.cron._write_crontab", side_effect=lambda t: written.setdefault("text", t)):
            entries = cron_module.install_cron(
                jobs=[manual_only],
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
            )
        self.assertEqual(entries, [])
        self.assertNotIn("# job:manual-only", written.get("text", ""))

    def test_preserves_lines_outside_block(self):
        existing = (
            "0 0 * * * /home/me/backup.sh\n"
            f"{BEGIN_MARKER}\n"
            "old line\n"
            f"{END_MARKER}\n"
            "5 * * * * /usr/bin/something\n"
        )
        written: dict = {}
        with patch("content_parser.jobs.cron._existing_crontab", return_value=existing), \
             patch("content_parser.jobs.cron._write_crontab", side_effect=lambda t: written.setdefault("text", t)):
            cron_module.install_cron(
                jobs=[self._job()],
                project_root=Path("/repo"),
                python_executable="/usr/bin/python3",
            )
        text = written["text"]
        self.assertIn("/home/me/backup.sh", text)
        self.assertIn("/usr/bin/something", text)
        self.assertNotIn("old line", text)


class RemoveCronTest(unittest.TestCase):
    def test_removes_when_present(self):
        existing = (
            f"0 0 * * * /backup\n"
            f"{BEGIN_MARKER}\n"
            "managed line\n"
            f"{END_MARKER}\n"
        )
        written: dict = {}
        with patch("content_parser.jobs.cron._existing_crontab", return_value=existing), \
             patch("content_parser.jobs.cron._write_crontab", side_effect=lambda t: written.setdefault("text", t)):
            self.assertTrue(cron_module.remove_cron())
        text = written["text"]
        self.assertNotIn(BEGIN_MARKER, text)
        self.assertIn("/backup", text)

    def test_returns_false_when_absent(self):
        with patch("content_parser.jobs.cron._existing_crontab", return_value="0 0 * * * /backup\n"), \
             patch("content_parser.jobs.cron._write_crontab") as wr:
            self.assertFalse(cron_module.remove_cron())
            wr.assert_not_called()


class ReadBlockTest(unittest.TestCase):
    def test_parses_managed_entries(self):
        existing = (
            "0 0 * * * outside\n"
            f"{BEGIN_MARKER}\n"
            "0 6 * * MON cd /x && py -m content_parser.cli jobs run weekly  # job:weekly\n"
            "0 8 * * * cd /x && py -m content_parser.cli jobs run daily  # job:daily\n"
            f"{END_MARKER}\n"
            "5 * * * * also-outside\n"
        )
        with patch("content_parser.jobs.cron._existing_crontab", return_value=existing):
            entries = cron_module.read_block()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].schedule, "0 6 * * MON")
        self.assertEqual(entries[0].job_name, "weekly")
        self.assertEqual(entries[1].schedule, "0 8 * * *")
        self.assertEqual(entries[1].job_name, "daily")

    def test_empty_when_no_block(self):
        with patch("content_parser.jobs.cron._existing_crontab", return_value="0 0 * * * x\n"):
            self.assertEqual(cron_module.read_block(), [])


class ExistingCrontabTest(unittest.TestCase):
    def test_no_crontab_returns_empty(self):
        proc = type("P", (), {})()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "no crontab for user\n"
        with patch("content_parser.jobs.cron.subprocess.run", return_value=proc):
            self.assertEqual(cron_module._existing_crontab(), "")

    def test_real_error_raises(self):
        proc = type("P", (), {})()
        proc.returncode = 1
        proc.stdout = ""
        proc.stderr = "crontab: invalid option -- 'z'"
        with patch("content_parser.jobs.cron.subprocess.run", return_value=proc):
            with self.assertRaises(CronError):
                cron_module._existing_crontab()

    def test_no_crontab_binary_raises(self):
        with patch("content_parser.jobs.cron.subprocess.run",
                   side_effect=FileNotFoundError("crontab not installed")):
            with self.assertRaises(CronError) as cm:
                cron_module._existing_crontab()
            self.assertIn("crontab", str(cm.exception).lower())


if __name__ == "__main__":
    unittest.main()
