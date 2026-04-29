"""Tests for content_parser.jobs.schema — Job parsing, validation, YAML I/O."""
from __future__ import annotations

import unittest

from content_parser.core.errors import PluginError
from content_parser.jobs.schema import (
    Job,
    SheetInput,
    dump_job_yaml,
    is_valid_cron,
    load_job_yaml,
)


VALID_YAML = """\
name: weekly-vk
source: vk
schedule: "0 6 * * MON"
description: "Weekly VK marketing"
inputs:
  community: [durov_says, telegram]
sheet_inputs:
  - sheet: "https://docs.google.com/spreadsheets/d/abc/edit"
    tab: Communities
    range: A2:A
    target: community
settings:
  max_posts_per_input: 50
"""


class CronValidationTest(unittest.TestCase):
    def test_standard_5_token(self):
        self.assertTrue(is_valid_cron("0 6 * * MON"))
        self.assertTrue(is_valid_cron("*/15 * * * *"))
        self.assertTrue(is_valid_cron("0 0,12 * * *"))
        self.assertTrue(is_valid_cron("0 9-17 * * 1-5"))

    def test_aliases(self):
        for alias in ("@yearly", "@annually", "@monthly", "@weekly", "@daily", "@hourly", "@reboot"):
            with self.subTest(alias=alias):
                self.assertTrue(is_valid_cron(alias))

    def test_invalid_token_count(self):
        self.assertFalse(is_valid_cron("0 6 * *"))     # 4 tokens
        self.assertFalse(is_valid_cron("0 6 * * MON extra"))

    def test_invalid_chars(self):
        self.assertFalse(is_valid_cron("0 6 * * !"))
        self.assertFalse(is_valid_cron("0 6 * * MON; rm -rf /"))

    def test_unknown_alias(self):
        self.assertFalse(is_valid_cron("@bogus"))

    def test_non_string(self):
        self.assertFalse(is_valid_cron(None))   # type: ignore[arg-type]
        self.assertFalse(is_valid_cron(42))     # type: ignore[arg-type]


class JobValidationTest(unittest.TestCase):
    def _base(self, **overrides):
        kwargs = dict(
            name="my-job",
            source="vk",
            inputs={"community": ["durov_says"]},
        )
        kwargs.update(overrides)
        return Job(**kwargs)

    def test_minimal_valid(self):
        self._base().validate()  # no exception

    def test_invalid_name_chars(self):
        with self.assertRaises(PluginError):
            self._base(name="my job").validate()
        with self.assertRaises(PluginError):
            self._base(name="../etc").validate()
        with self.assertRaises(PluginError):
            self._base(name="").validate()

    def test_name_too_long(self):
        with self.assertRaises(PluginError):
            self._base(name="x" * 65).validate()

    def test_missing_source(self):
        with self.assertRaises(PluginError):
            self._base(source="").validate()

    def test_no_inputs_and_no_sheets_rejected(self):
        with self.assertRaises(PluginError):
            self._base(inputs={}).validate()

    def test_only_sheet_inputs_ok(self):
        self._base(
            inputs={},
            sheet_inputs=[SheetInput(sheet="X" * 25, target="community")],
        ).validate()

    def test_invalid_schedule(self):
        with self.assertRaises(PluginError):
            self._base(schedule="not a cron").validate()

    def test_inputs_not_list_rejected(self):
        with self.assertRaises(PluginError):
            self._base(inputs={"community": "not a list"}).validate()  # type: ignore[arg-type]

    def test_output_dir_with_dotdot_rejected(self):
        # Path traversal: 'output_dir: ../../etc' rejected at validation.
        with self.assertRaises(PluginError) as cm:
            self._base(output_dir="../../etc").validate()
        self.assertIn("..", str(cm.exception))

    def test_output_dir_normal_relative_ok(self):
        self._base(output_dir="custom/scheduled").validate()  # no exception

    def test_output_dir_absolute_ok(self):
        self._base(output_dir="/tmp/my-output").validate()  # user explicitly opted in

    def test_output_dir_dotdot_in_middle_rejected(self):
        # ../../ at any position is rejected, not just at start.
        with self.assertRaises(PluginError):
            self._base(output_dir="custom/../escape").validate()

    def test_sheet_input_missing_sheet(self):
        with self.assertRaises(PluginError):
            self._base(
                sheet_inputs=[SheetInput(sheet="", target="community")],
            ).validate()

    def test_sheet_input_missing_target(self):
        with self.assertRaises(PluginError):
            self._base(
                sheet_inputs=[SheetInput(sheet="X" * 25, target="")],
            ).validate()

    def test_invalid_notify_value(self):
        with self.assertRaises(PluginError):
            self._base(notify_on_failure="email").validate()


class YamlRoundTripTest(unittest.TestCase):
    def test_load_full(self):
        job = load_job_yaml(VALID_YAML)
        self.assertEqual(job.name, "weekly-vk")
        self.assertEqual(job.source, "vk")
        self.assertEqual(job.schedule, "0 6 * * MON")
        self.assertEqual(job.inputs, {"community": ["durov_says", "telegram"]})
        self.assertEqual(len(job.sheet_inputs), 1)
        self.assertEqual(job.sheet_inputs[0].target, "community")
        self.assertEqual(job.sheet_inputs[0].range_a1, "A2:A")
        self.assertEqual(job.settings, {"max_posts_per_input": 50})

    def test_dump_then_load_idempotent(self):
        original = load_job_yaml(VALID_YAML)
        dumped = dump_job_yaml(original)
        reloaded = load_job_yaml(dumped)
        self.assertEqual(reloaded.name, original.name)
        self.assertEqual(reloaded.source, original.source)
        self.assertEqual(reloaded.schedule, original.schedule)
        self.assertEqual(reloaded.inputs, original.inputs)
        self.assertEqual(len(reloaded.sheet_inputs), 1)

    def test_uses_safe_load(self):
        # YAML with a !!python/object directive must be rejected by safe_load.
        evil = """\
!!python/object/apply:os.system
- 'echo PWNED'
"""
        with self.assertRaises(PluginError):
            load_job_yaml(evil)

    def test_empty_doc_rejected(self):
        with self.assertRaises(PluginError):
            load_job_yaml("")

    def test_top_level_list_rejected(self):
        with self.assertRaises(PluginError):
            load_job_yaml("- a\n- b\n")

    def test_name_hint_used_when_body_missing_name(self):
        yaml_no_name = """\
source: vk
inputs:
  community: [durov_says]
"""
        job = load_job_yaml(yaml_no_name, name_hint="from-filename")
        self.assertEqual(job.name, "from-filename")

    def test_range_alt_key_supported(self):
        # Some users will write `range_a1` instead of `range`
        yaml_alt = """\
name: x
source: vk
sheet_inputs:
  - sheet: longidlongidlongidlongidlong
    target: community
    range_a1: B:B
"""
        job = load_job_yaml(yaml_alt)
        self.assertEqual(job.sheet_inputs[0].range_a1, "B:B")

    def test_string_value_in_inputs_rejected(self):
        # Common typo: `community: durov_says` (no brackets) — without the
        # type-check the loop would iterate the string character by character
        # and produce ['d','u','r','o','v', ...]. Must raise instead.
        evil = """\
name: my-job
source: vk
inputs:
  community: durov_says
"""
        with self.assertRaises(PluginError) as cm:
            load_job_yaml(evil)
        self.assertIn("must be a list", str(cm.exception))
        self.assertIn("community", str(cm.exception))

    def test_int_value_in_inputs_rejected(self):
        evil = """\
name: x
source: vk
inputs:
  community: 42
"""
        with self.assertRaises(PluginError):
            load_job_yaml(evil)

    def test_dict_value_in_inputs_rejected(self):
        evil = """\
name: x
source: vk
inputs:
  community: {nested: dict}
"""
        with self.assertRaises(PluginError):
            load_job_yaml(evil)

    def test_empty_value_in_inputs_treated_as_empty_list(self):
        # `inputs.community:` with no value yields None. Acceptable as empty list.
        yaml_empty = """\
name: x
source: vk
inputs:
  community:
sheet_inputs:
  - sheet: longidlongidlongidlongidlong
    target: community
"""
        job = load_job_yaml(yaml_empty)
        self.assertEqual(job.inputs["community"], [])


class OutputDirTest(unittest.TestCase):
    def test_default_output_dir(self):
        job = Job(name="my-job", source="vk", inputs={"community": ["x"]})
        path = job.resolved_output_dir(timestamp="20260101_120000")
        self.assertEqual(path.parts[-3:], ("scheduled", "my-job", "20260101_120000"))

    def test_relative_output_dir_resolved_against_cwd(self):
        job = Job(
            name="my-job", source="vk",
            inputs={"community": ["x"]},
            output_dir="custom/path",
        )
        path = job.resolved_output_dir(timestamp="20260101_120000")
        self.assertTrue(path.is_absolute())
        self.assertEqual(path.parts[-3:], ("custom", "path", "20260101_120000"))

    def test_absolute_output_dir_used_as_is(self):
        job = Job(
            name="my-job", source="vk",
            inputs={"community": ["x"]},
            output_dir="/tmp/abs/path",
        )
        path = job.resolved_output_dir(timestamp="20260101_120000")
        self.assertEqual(str(path), "/tmp/abs/path/20260101_120000")


if __name__ == "__main__":
    unittest.main()
