"""Tests for content_parser.transcription.runner — the maybe_transcribe orchestrator."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_parser.core.schema import Item, Transcript
from content_parser.transcription import cache as cache_mod
from content_parser.transcription import runner as runner_mod


WHISPER_RESPONSE = {
    "text": "Hello world",
    "language": "en",
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.5, "text": "Hello world"},
        {"id": 1, "start": 2.5, "end": 5.0, "text": "more text"},
    ],
}


class MaybeTranscribeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cp_run_"))
        self._orig = cache_mod.CACHE_DIR
        cache_mod.CACHE_DIR = self.tmp / "cache"

    def tearDown(self):
        cache_mod.CACHE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _item(self, **kw):
        defaults = dict(
            source="instagram", item_id="AAA",
            url="https://www.instagram.com/p/AAA/",
            media={"video_url": "https://cdn.example/v.mp4"},
        )
        defaults.update(kw)
        return Item(**defaults)

    def test_disabled_setting_does_nothing(self):
        item = self._item()
        runner_mod.maybe_transcribe(item, settings={"transcribe_videos": False}, secrets={})
        self.assertIsNone(item.transcript)

    def test_no_api_key_sets_error_transcript(self):
        item = self._item()
        runner_mod.maybe_transcribe(
            item,
            settings={"transcribe_videos": True},
            secrets={"OPENAI_API_KEY": ""},
        )
        self.assertIsNotNone(item.transcript)
        self.assertIn("OPENAI_API_KEY", item.transcript.error)
        self.assertEqual(item.transcript.segments, [])

    def test_uses_cache_skipping_download_and_api(self):
        cache_mod.put("instagram", "AAA", {
            "language": "ru", "is_generated": True,
            "segments": [{"start": 0.0, "duration": 1.0, "text": "cached"}],
            "text": "cached", "error": None,
        })
        item = self._item()
        with patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio") as ta:
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
            )
        dl.assert_not_called()
        ta.assert_not_called()
        self.assertEqual(item.transcript.text, "cached")

    def test_full_pipeline_downloads_transcribes_caches(self):
        item = self._item()
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=30.0), \
             patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio", return_value=WHISPER_RESPONSE):
            audio_path = self.tmp / "fake.mp3"
            audio_path.write_bytes(b"\x00")
            dl.return_value = audio_path

            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True, "max_audio_seconds_per_video": 600},
                secrets={"OPENAI_API_KEY": "k"},
            )

        self.assertIsNotNone(item.transcript)
        self.assertEqual(item.transcript.text, "Hello world")
        self.assertEqual(item.transcript.language, "en")
        self.assertEqual(len(item.transcript.segments), 2)
        self.assertEqual(item.transcript.segments[0]["start"], 0.0)
        self.assertEqual(item.transcript.segments[0]["duration"], 2.5)
        # Cache populated
        cached = cache_mod.get("instagram", "AAA")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["text"], "Hello world")

    def test_duration_cap_skips_download(self):
        item = self._item()
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=1200.0), \
             patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio") as ta:
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True, "max_audio_seconds_per_video": 600},
                secrets={"OPENAI_API_KEY": "k"},
            )
        dl.assert_not_called()
        ta.assert_not_called()
        self.assertIn("too long", item.transcript.error.lower())

    def test_download_failure_recorded_in_error(self):
        from content_parser.transcription.downloader import DownloadError
        item = self._item()
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=30.0), \
             patch("content_parser.transcription.runner.download_audio", side_effect=DownloadError("nope")), \
             patch("content_parser.transcription.runner.transcribe_audio") as ta:
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
            )
        ta.assert_not_called()
        self.assertIn("download", item.transcript.error.lower())

    def test_whisper_failure_recorded_in_error(self):
        from content_parser.transcription.whisper_api import WhisperError
        item = self._item()
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=30.0), \
             patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio", side_effect=WhisperError("api died")):
            audio_path = self.tmp / "fake.mp3"
            audio_path.write_bytes(b"\x00")
            dl.return_value = audio_path
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
            )
        self.assertIn("whisper", item.transcript.error.lower())

    def test_only_if_missing_skips_when_transcript_present(self):
        item = self._item()
        item.transcript = Transcript(
            language="ru", is_generated=False,
            segments=[{"start": 0.0, "duration": 1.0, "text": "existing"}],
            text="existing", error=None,
        )
        with patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio") as ta:
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
                only_if_missing=True,
            )
        dl.assert_not_called()
        ta.assert_not_called()
        self.assertEqual(item.transcript.text, "existing")  # untouched

    def test_only_if_missing_runs_when_segments_empty(self):
        item = self._item()
        item.transcript = Transcript(
            language=None, is_generated=None, segments=[], text="",
            error="blocked",  # earlier youtube-transcript-api fail
        )
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=30.0), \
             patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio", return_value=WHISPER_RESPONSE):
            audio_path = self.tmp / "fake.mp3"
            audio_path.write_bytes(b"\x00")
            dl.return_value = audio_path
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
                only_if_missing=True,
            )
        # Was filled by Whisper now
        self.assertEqual(item.transcript.text, "Hello world")
        self.assertEqual(len(item.transcript.segments), 2)

    def test_no_video_url_silent_skip(self):
        item = self._item(media={}, url="")
        runner_mod.maybe_transcribe(
            item,
            settings={"transcribe_videos": True},
            secrets={"OPENAI_API_KEY": "k"},
        )
        self.assertIsNone(item.transcript)

    def test_prefers_canonical_url_over_cdn(self):
        # Instagram CDN URL in media.video_url, but post URL in item.url.
        # We should prefer the post URL — yt-dlp can re-fetch fresh CDN links.
        item = self._item(
            url="https://www.instagram.com/reel/AAA/",
            media={"video_url": "https://expired-cdn.example/x.mp4?token=old"},
        )
        with patch("content_parser.transcription.runner.get_duration_seconds", return_value=30.0), \
             patch("content_parser.transcription.runner.download_audio") as dl, \
             patch("content_parser.transcription.runner.transcribe_audio", return_value=WHISPER_RESPONSE):
            audio_path = self.tmp / "fake.mp3"
            audio_path.write_bytes(b"\x00")
            dl.return_value = audio_path
            runner_mod.maybe_transcribe(
                item,
                settings={"transcribe_videos": True},
                secrets={"OPENAI_API_KEY": "k"},
            )
        url_passed = dl.call_args.args[0]
        self.assertIn("instagram.com", url_passed)
        self.assertNotIn("expired-cdn", url_passed)


if __name__ == "__main__":
    unittest.main()
