"""Fetch transcripts using youtube-transcript-api (no API quota cost)."""
from __future__ import annotations

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)
from youtube_transcript_api._errors import VideoUnavailable


def fetch_transcript(
    video_id: str, languages: list[str] | None = None
) -> dict | None:
    """Return transcript info for a video, or None if unavailable.

    Tries the requested languages in order, falls back to any available
    transcript (translating to the first preferred language if needed).
    """
    preferred = languages or ["ru", "en"]

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except (TranscriptsDisabled, VideoUnavailable):
        return None
    except Exception:
        return None

    transcript = None
    try:
        transcript = transcript_list.find_manually_created_transcript(preferred)
    except NoTranscriptFound:
        pass

    if transcript is None:
        try:
            transcript = transcript_list.find_generated_transcript(preferred)
        except NoTranscriptFound:
            pass

    if transcript is None:
        try:
            any_t = next(iter(transcript_list))
            if any_t.is_translatable:
                transcript = any_t.translate(preferred[0])
            else:
                transcript = any_t
        except (StopIteration, NoTranscriptFound):
            return None

    try:
        segments = transcript.fetch()
    except Exception:
        return None

    return {
        "language": transcript.language_code,
        "is_generated": transcript.is_generated,
        "segments": [
            {
                "start": float(s["start"]),
                "duration": float(s.get("duration", 0)),
                "text": s["text"],
            }
            for s in segments
        ],
        "text": " ".join(s["text"].strip() for s in segments if s["text"].strip()),
    }
