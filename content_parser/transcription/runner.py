"""Orchestrate per-item transcription: cache check → download audio → Whisper.

Plugins call `maybe_transcribe(item, settings, secrets)` once per Item they
yield. The function is a no-op when transcription is disabled or no audio
URL is available; on hard errors it sets `item.transcript.error` so the
output still records why.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..core.schema import Item, Transcript
from . import cache as cache_mod
from .downloader import DownloadError, download_audio, get_duration_seconds
from .whisper_api import WhisperError, transcribe_audio


def _transcript_from_whisper_response(resp: dict) -> Transcript:
    segments_raw = resp.get("segments") or []
    segments = []
    for seg in segments_raw:
        start = float(seg.get("start", 0.0) or 0.0)
        end = float(seg.get("end", start) or start)
        segments.append({
            "start": start,
            "duration": max(end - start, 0.0),
            "text": seg.get("text", ""),
        })
    text = (resp.get("text") or "").strip()
    if not text and segments:
        text = " ".join(s["text"].strip() for s in segments if s["text"].strip())
    return Transcript(
        language=resp.get("language"),
        is_generated=True,         # Whisper output is always machine-generated
        segments=segments,
        text=text,
        error=None,
    )


def _transcript_to_dict(t: Transcript) -> dict:
    return {
        "language": t.language,
        "is_generated": t.is_generated,
        "segments": list(t.segments),
        "text": t.text,
        "error": t.error,
    }


def _video_url_for(item: Item) -> str | None:
    """Pick the best URL for audio extraction.

    Some plugins put a direct CDN URL in media.video_url, which yt-dlp can't
    always re-fetch (cookies/expiry). For Instagram/TikTok/Telegram, the
    canonical post URL works better — yt-dlp resolves the media URL fresh.
    """
    # Prefer a recognizable platform URL over a CDN URL.
    if item.url and any(host in item.url for host in (
        "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
        "vk.com", "vk.ru", "t.me", "telegram.me",
    )):
        return item.url
    return item.media.get("video_url") or item.url or None


def maybe_transcribe(
    item: Item,
    settings: dict[str, Any],
    secrets: dict[str, str],
    *,
    only_if_missing: bool = False,
) -> None:
    """Populate item.transcript from Whisper if conditions are met.

    only_if_missing: skip when item.transcript already has segments. Used by
    YouTube where youtube-transcript-api ran first; we only fall back to
    Whisper when subtitles weren't available.
    """
    if not settings.get("transcribe_videos"):
        return
    if only_if_missing and item.transcript and item.transcript.segments:
        return

    api_key = (secrets.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        item.transcript = Transcript(
            error="OPENAI_API_KEY not set; cannot run Whisper.",
            language=None, is_generated=None, segments=[], text="",
        )
        return

    video_url = _video_url_for(item)
    if not video_url:
        return  # silently skip — no media to transcribe

    # Cache lookup before any network/download.
    cached = cache_mod.get(item.source, item.item_id)
    if cached:
        item.transcript = Transcript(
            language=cached.get("language"),
            is_generated=cached.get("is_generated"),
            segments=list(cached.get("segments") or []),
            text=cached.get("text") or "",
            error=cached.get("error"),
        )
        return

    # Per-item duration cap to keep Whisper bills bounded.
    max_seconds = int(settings.get("max_audio_seconds_per_video", 600) or 600)
    duration = get_duration_seconds(video_url)
    if duration is not None and duration > max_seconds:
        item.transcript = Transcript(
            error=f"video too long: {int(duration)}s > {max_seconds}s cap (transcription skipped).",
            language=None, is_generated=None, segments=[], text="",
        )
        return

    with tempfile.TemporaryDirectory(prefix="cp_audio_") as tmp:
        try:
            audio_path = download_audio(video_url, Path(tmp))
        except DownloadError as e:
            item.transcript = Transcript(
                error=f"download failed: {e}",
                language=None, is_generated=None, segments=[], text="",
            )
            return

        try:
            response = transcribe_audio(audio_path, api_key)
        except WhisperError as e:
            item.transcript = Transcript(
                error=f"whisper failed: {e}",
                language=None, is_generated=None, segments=[], text="",
            )
            return

    transcript = _transcript_from_whisper_response(response)
    item.transcript = transcript

    try:
        cache_mod.put(item.source, item.item_id, _transcript_to_dict(transcript))
    except OSError:
        pass  # cache is best-effort
