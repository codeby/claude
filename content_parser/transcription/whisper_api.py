"""Thin HTTP client for OpenAI's Whisper transcription endpoint.

We avoid the official `openai` Python package because it's a heavy dep with
its own dependencies. Whisper takes one POST: file in multipart, model name
+ optional language + response_format in the form fields.

Pricing as of 2026-04: $0.006/minute of audio. The 25 MB upload limit is
enforced by OpenAI; we cap on the download side too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import requests


WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"


class WhisperError(Exception):
    pass


def transcribe_audio(
    audio_path: Path,
    api_key: str,
    *,
    language: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Send an audio file to Whisper. Returns OpenAI's verbose_json shape.

    The verbose_json format gives us segments with start/end timestamps,
    matching what the existing youtube-transcript-api adapter produces.
    """
    if not api_key:
        raise WhisperError("OPENAI_API_KEY is required for Whisper transcription.")
    if not audio_path.exists():
        raise WhisperError(f"Audio file does not exist: {audio_path}")

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
        raise WhisperError("OpenAI rate-limit (429). Wait and retry.")
    if not r.ok:
        # OpenAI puts the error message in {"error": {"message": "..."}}
        try:
            err = r.json().get("error", {}).get("message", r.text[:200])
        except ValueError:
            err = r.text[:200]
        raise WhisperError(f"Whisper returned {r.status_code}: {err}")

    try:
        return r.json()
    except ValueError as e:
        raise WhisperError(f"Whisper returned non-JSON: {r.text[:200]}") from e
