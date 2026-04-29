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
        with patch("content_parser.transcription.whisper_api.requests.post") as rp:
            rp.return_value = _mock_response({}, ok=False, status=429)
            with self.assertRaises(WhisperError) as cm:
                transcribe_audio(self.audio, "k")
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


if __name__ == "__main__":
    unittest.main()
