"""Tests for youtube_parser.main — legacy CLI translation to new content_parser.cli."""
from __future__ import annotations

import unittest

from youtube_parser.main import _build_legacy_parser, _to_new_argv


class LegacyCliTest(unittest.TestCase):
    def _translate(self, args_list):
        ns = _build_legacy_parser().parse_args(args_list)
        return _to_new_argv(ns)

    def test_basic_video(self):
        new = self._translate(["--video", "https://youtu.be/x"])
        self.assertIn("run", new)
        self.assertIn("--source", new)
        self.assertIn("youtube", new)
        self.assertIn("--video", new)
        self.assertIn("https://youtu.be/x", new)

    def test_output_passed(self):
        new = self._translate(["--video", "x", "--output", "/tmp/out"])
        self.assertIn("--output", new)
        self.assertIn("/tmp/out", new)

    def test_settings_translated(self):
        new = self._translate([
            "--video", "x",
            "--max-comments", "50",
            "--include-replies",
            "--no-transcripts",
            "--comment-order", "time",
        ])
        self.assertIn("max_comments=50", new)
        self.assertIn("include_replies=true", new)
        self.assertIn("fetch_transcripts=false", new)
        self.assertIn("comment_order=time", new)

    def test_no_comments_flag(self):
        new = self._translate(["--video", "x", "--no-comments"])
        self.assertIn("fetch_comments=false", new)

    def test_default_settings_when_unset(self):
        new = self._translate(["--video", "x"])
        self.assertIn("fetch_transcripts=true", new)
        self.assertIn("fetch_comments=true", new)
        self.assertIn("include_replies=false", new)


if __name__ == "__main__":
    unittest.main()
