"""Tests for content_parser.transcription.whisper_api — HTTP client behavior."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from content_parser.transcription.whisper_api import WhisperError, transcribe_audio


def _mock_response(payload, *, ok=True, status=200):
    m = MagicMock()
    m.ok = ok
    m.status_code = status
    m.json.return_value = payload
    m.text = str(payload)
    return m


class TranscribeAudioTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.audio = Path(self.tmpdir.name) / "x.mp3"
        self.audio.write_bytes(b"\x00" * 100)  # any bytes — we mock the HTTP call

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_missing_api_key_raises(self):
        with self.assertRaises(WhisperError):
            transcribe_audio(self.audio, "")

    def test_missing_file_raises(self):
        with self.assertRaises(WhisperError):
            transcribe_audio(Path("/nonexistent.mp3"), "k")

    def test_uses_bearer_header(self):
        payload = {"text": "hello", "language": "en", "segments": []}
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response(payload)
            transcribe_audio(self.audio, "MY_TOKEN")
            kwargs = rp.call_args.kwargs
            self.assertEqual(kwargs["headers"], {"Authorization": "Bearer MY_TOKEN"})

    def test_sends_verbose_json_format(self):
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response({"text": "x", "segments": []})
            transcribe_audio(self.audio, "k")
            data = rp.call_args.kwargs["data"]
            self.assertEqual(data["model"], "whisper-1")
            self.assertEqual(data["response_format"], "verbose_json")
            self.assertEqual(data["timestamp_granularities[]"], "segment")

    def test_language_optional(self):
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response({"text": "x", "segments": []})
            transcribe_audio(self.audio, "k", language="ru")
            data = rp.call_args.kwargs["data"]
            self.assertEqual(data["language"], "ru")

    def test_401_message_explicit(self):
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response({}, ok=False, status=401)
            with self.assertRaises(WhisperError) as cm:
                transcribe_audio(self.audio, "bad")
            self.assertIn("API key", str(cm.exception))

    def test_429_rate_limit(self):
        # 429 is now retried; with max_retries=0 we get a single attempt and a raise.
        with patch("content_parser.transcription.whisper_api.requests.post") as rp, \
             patch("content_parser.transcription.whisper_api._sleep"):
            rp.return_value = _mock_response({}, ok=False, status=429)
            with self.assertRaises(WhisperError) as cm:
                transcribe_audio(self.audio, "k", max_retries=0)
            self.assertIn("rate-limit", str(cm.exception).lower())

    def test_other_error_includes_message(self):
        resp = _mock_response({"error": {"message": "Bad request: file too short"}}, ok=False, status=400)
        with patch("content_parser.transcription.whisper_api.requests.post", return_value=resp):
            with self.assertRaises(WhisperError) as cm:
                transcribe_audio(self.audio, "k")
            self.assertIn("Bad request", str(cm.exception))
            self.assertIn("400", str(cm.exception))

    def test_returns_parsed_json(self):
        payload = {
            "text": "Hello world",
            "language": "en",
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.5, "text": "Hello world"},
            ],
        }
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response(payload)
            result = transcribe_audio(self.audio, "k")
            self.assertEqual(result["text"], "Hello world")
            self.assertEqual(len(result["segments"]), 1)


class RetryTest(unittest.TestCase):
    """Whisper retries 429 and 5xx with exponential backoff."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.audio = Path(self.tmpdir.name) / "x.mp3"
        self.audio.write_bytes(b"\x00" * 100)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_429_then_success(self):
        sleeps: list[float] = []
        responses = [
            _mock_response({}, ok=False, status=429),
            _mock_response({"text": "ok", "segments": []}),
        ]
        with patch("content_parser.transcription.whisper_api.requests.post", side_effect=responses) as rp, \
             patch("content_parser.transcription.whisper_api._sleep", side_effect=sleeps.append):
            result = transcribe_audio(self.audio, "k")
        self.assertEqual(rp.call_count, 2)
        self.assertEqual(sleeps, [2.0])
        self.assertEqual(result["text"], "ok")

    def test_500_then_503_then_success(self):
        sleeps: list[float] = []
        responses = [
            _mock_response({}, ok=False, status=500),
            _mock_response({}, ok=False, status=503),
            _mock_response({"text": "ok", "segments": []}),
        ]
        with patch("content_parser.transcription.whisper_api.requests.post", side_effect=responses) as rp, \
             patch("content_parser.transcription.whisper_api._sleep", side_effect=sleeps.append):
            transcribe_audio(self.audio, "k")
        self.assertEqual(rp.call_count, 3)
        self.assertEqual(sleeps, [2.0, 4.0])  # exponential

    def test_429_exhausts_retries_then_raises(self):
        responses = [_mock_response({}, ok=False, status=429)] * 5  # plenty
        with patch("content_parser.transcription.whisper_api.requests.post", side_effect=responses) as rp, \
             patch("content_parser.transcription.whisper_api._sleep"):
            with self.assertRaises(WhisperError):
                transcribe_audio(self.audio, "k", max_retries=2)
        # Initial + 2 retries = 3 calls.
        self.assertEqual(rp.call_count, 3)

    def test_401_does_not_retry(self):
        responses = [_mock_response({}, ok=False, status=401)] * 3
        with patch("content_parser.transcription.whisper_api.requests.post", side_effect=responses) as rp, \
             patch("content_parser.transcription.whisper_api._sleep"):
            with self.assertRaises(WhisperError):
                transcribe_audio(self.audio, "bad")
        self.assertEqual(rp.call_count, 1)

    def test_400_does_not_retry(self):
        responses = [_mock_response({"error": {"message": "bad audio"}}, ok=False, status=400)] * 3
        with patch("content_parser.transcription.whisper_api.requests.post", side_effect=responses) as rp, \
             patch("content_parser.transcription.whisper_api._sleep"):
            with self.assertRaises(WhisperError):
                transcribe_audio(self.audio, "k")
        self.assertEqual(rp.call_count, 1)


if __name__ == "__main__":
    unittest.main()
