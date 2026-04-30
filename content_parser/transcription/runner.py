"""Orchestrate per-item transcription: cache check → download audio → Whisper.

Plugins call `maybe_transcribe(item, settings, secrets)` once per Item they
yield. The function is a no-op when transcription is disabled or no audio
URL is available; on hard errors it sets `item.transcript.error` so the
output still records why.
"""
from __future__ import annotations

import tempfile
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..core.schema import Item, Transcript
from . import cache as cache_mod
from .downloader import DownloadError, download_audio, get_duration_seconds
from .whisper_api import WhisperError, transcribe_audio


# Hostnames that should never reach yt-dlp. Anything else gets parsed and
# checked via the ipaddress module if it looks like an IP literal.
_DENIED_HOSTNAMES = {"localhost", "0.0.0.0", "ip6-localhost", "ip6-loopback"}


def _is_public_url(url: str) -> bool:
    """Reject URLs that point at the local machine or RFC1918 networks.

    yt-dlp would happily fetch from a URL like 'http://169.254.169.254/...'
    (AWS metadata) or 'http://10.0.0.1/...' if any of our third-party
    sources (Apify/VK/Telegram) ever returned one. This guard rejects:
      - non-http(s) schemes
      - 'localhost' and well-known loopback hostnames
      - IPv4/IPv6 literals that resolve to loopback / private / link-local /
        reserved space

    Note: bare DNS names that resolve to private IPs are NOT caught here
    (DNS rebinding). Mitigating that needs name resolution + connection
    pinning, which is yt-dlp's domain.
    """
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in _DENIED_HOSTNAMES:
        return False
    # If the hostname is an IP literal, classify it.
    try:
        ip = ip_address(host)
    except ValueError:
        return True  # ordinary DNS name — accept
    return not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast)


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

    # SSRF guard: refuse to send loopback / RFC1918 / link-local URLs to
    # yt-dlp, which would otherwise fetch from internal networks if any
    # upstream API returned such a URL (chain-of-trust risk).
    if not _is_public_url(video_url):
        item.transcript = Transcript(
            error=f"refused to fetch non-public URL: {video_url[:60]}",
            language=None, is_generated=None, segments=[], text="",
        )
        return

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

    # Per-item duration cap. We BLOCK if duration is unknown — without a
    # known length we can't bound the Whisper bill, so refusing is the
    # cheap-and-safe default. Power users who really need to transcribe
    # platforms where yt-dlp can't probe metadata can bypass by saving
    # the audio file and calling whisper_api directly.
    max_seconds = int(settings.get("max_audio_seconds_per_video", 600) or 600)
    duration = get_duration_seconds(video_url)
    if duration is None:
        item.transcript = Transcript(
            error="video duration unknown; transcription skipped to avoid unbounded cost.",
            language=None, is_generated=None, segments=[], text="",
        )
        return
    if duration > max_seconds:
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
