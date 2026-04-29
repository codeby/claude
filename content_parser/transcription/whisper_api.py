"""Thin HTTP client for OpenAI's Whisper transcription endpoint.

We avoid the official `openai` Python package because it's a heavy dep with
its own dependencies. Whisper takes one POST: file in multipart, model name
+ optional language + response_format in the form fields.

Pricing as of 2026-04: $0.006/minute of audio. The 25 MB upload limit is
enforced by OpenAI; we cap on the download side too.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests


WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"


class WhisperError(Exception):
    pass


class _RetryableWhisperError(WhisperError):
    """Internal: subclass for 429 / 5xx responses that warrant a retry."""


def transcribe_audio(
    audio_path: Path,
    api_key: str,
    *,
    language: str | None = None,
    timeout: int = 300,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Send an audio file to Whisper, retrying transient failures.

    Returns OpenAI's verbose_json shape (text, language, segments). 429
    (rate limit) and 5xx responses are retried up to `max_retries` times
    with exponential backoff (2s, 4s, 8s). 401, 4xx (other), and other
    WhisperError subclasses surface immediately.
    """
    if not api_key:
        raise WhisperError("OPENAI_API_KEY is required for Whisper transcription.")
    if not audio_path.exists():
        raise WhisperError(f"Audio file does not exist: {audio_path}")

    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return _transcribe_once(audio_path, api_key, language=language, timeout=timeout)
        except _RetryableWhisperError:
            if attempt >= max_retries:
                raise
            _sleep(delay)
            delay *= 2
    # Defensive — only reachable if max_retries < 0.
    raise WhisperError("Whisper retry loop exhausted without a result.")  # pragma: no cover


def _sleep(seconds: float) -> None:
    """Indirection so tests can patch sleep without slowing the suite."""
    time.sleep(seconds)


def _transcribe_once(
    audio_path: Path,
    api_key: str,
    *,
    language: str | None,
    timeout: int,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    data: dict[str, Any] = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if language:
        data["language"] = language

    with audio_path.open("rb") as f:
        files = {"file": (audio_path.name, f, "audio/mpeg")}
        try:
            r = requests.post(WHISPER_URL, headers=headers, data=data, files=files, timeout=timeout)
        except requests.RequestException as e:
            raise WhisperError(f"Network error calling Whisper: {e}") from e

    if r.status_code == 401:
        raise WhisperError("OpenAI rejected the API key (401). Check OPENAI_API_KEY.")
    if r.status_code == 429:
        raise _RetryableWhisperError("OpenAI rate-limit (429).")
    if 500 <= r.status_code < 600:
        raise _RetryableWhisperError(f"OpenAI server error ({r.status_code}).")
    if not r.ok:
        # 4xx other than 401/429 — non-retryable client error.
        try:
            err = r.json().get("error", {}).get("message", r.text[:200])
        except ValueError:
            err = r.text[:200]
        raise WhisperError(f"Whisper returned {r.status_code}: {err}")

    try:
        return r.json()
    except ValueError as e:
        raise WhisperError(f"Whisper returned non-JSON: {r.text[:200]}") from e
