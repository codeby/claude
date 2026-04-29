"""Fetch transcripts via youtube-transcript-api (no API quota cost)."""
from __future__ import annotations

from typing import Any

from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)


def fetch_transcript(
    video_id: str,
    languages: list[str] | None = None,
    proxy_config: Any | None = None,
) -> dict | None:
    result = fetch_transcript_verbose(
        video_id, languages=languages, proxy_config=proxy_config
    )
    if result.get("error"):
        return None
    if not result.get("segments"):
        return None
    return result


def fetch_transcript_verbose(
    video_id: str,
    languages: list[str] | None = None,
    proxy_config: Any | None = None,
) -> dict:
    preferred = languages or ["ru", "en"]
    api = YouTubeTranscriptApi(proxy_config=proxy_config) if proxy_config else YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)
    except TranscriptsDisabled:
        return {"error": "disabled", "segments": [], "text": "", "language": None, "is_generated": None}
    except (VideoUnavailable, VideoUnplayable, AgeRestricted) as e:
        return {"error": f"unavailable: {type(e).__name__}", "segments": [], "text": "",
                "language": None, "is_generated": None}
    except (IpBlocked, RequestBlocked, YouTubeRequestFailed) as e:
        return {"error": f"blocked: {type(e).__name__}", "segments": [], "text": "",
                "language": None, "is_generated": None}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "segments": [], "text": "",
                "language": None, "is_generated": None}

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
                try:
                    transcript = any_t.translate(preferred[0])
                except Exception:
                    transcript = any_t
            else:
                transcript = any_t
        except StopIteration:
            return {"error": "not_found", "segments": [], "text": "",
                    "language": None, "is_generated": None}

    try:
        fetched = transcript.fetch()
    except (IpBlocked, RequestBlocked, YouTubeRequestFailed) as e:
        return {"error": f"blocked: {type(e).__name__}", "segments": [], "text": "",
                "language": transcript.language_code, "is_generated": transcript.is_generated}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "segments": [], "text": "",
                "language": transcript.language_code, "is_generated": transcript.is_generated}

    snippets = list(fetched)
    segments = [
        {
            "start": float(getattr(s, "start", 0.0)),
            "duration": float(getattr(s, "duration", 0.0)),
            "text": getattr(s, "text", ""),
        }
        for s in snippets
    ]

    return {
        "language": transcript.language_code,
        "is_generated": transcript.is_generated,
        "segments": segments,
        "text": " ".join(s["text"].strip() for s in segments if s["text"].strip()),
        "error": None,
    }
