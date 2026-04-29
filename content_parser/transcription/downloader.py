"""Pull audio from a public video URL via yt-dlp.

Used by the transcription pipeline. Requires `ffmpeg` available on PATH for
audio extraction. The downloaded file is small (≈300-500 KB for a 30-sec
reel as MP3), well under the 25 MB limit of OpenAI's Whisper API.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class DownloadError(Exception):
    """Raised when audio extraction fails (network, unavailable URL, no ffmpeg, ...)."""


def download_audio(url: str, target_dir: Path, *, max_filesize_mb: int = 24) -> Path:
    """Download audio from `url` into `target_dir`. Returns the resulting Path.

    `max_filesize_mb` caps the post-extraction file size; OpenAI's Whisper API
    limit is 25 MB, so we stay slightly under to leave room for headers.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError as e:
        raise DownloadError(
            "yt-dlp is not installed. Add yt-dlp to requirements.txt."
        ) from e

    target_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(target_dir / "%(id)s.%(ext)s")

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "max_filesize": max_filesize_mb * 1024 * 1024,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",  # 64 kbps is plenty for speech recognition
        }],
        # Be polite — these scrapers don't like aggressive concurrency.
        "concurrent_fragment_downloads": 1,
        "retries": 2,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        raise DownloadError(f"yt-dlp failed for {url!r}: {e}") from e

    # The post-processor changes the extension to .mp3.
    video_id = info.get("id") or "audio"
    candidate = target_dir / f"{video_id}.mp3"
    if candidate.exists():
        return candidate

    # Fallback: find any audio file we left in the dir
    for ext in ("mp3", "m4a", "webm", "opus", "wav"):
        for path in target_dir.glob(f"*.{ext}"):
            return path
    raise DownloadError(f"yt-dlp did not produce an audio file for {url!r}")


def get_duration_seconds(url: str) -> float | None:
    """Return the video's duration in seconds without downloading. None if unknown.

    Useful for budget gating before paying for transcription.
    """
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        return None

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    duration = info.get("duration")
    if duration is None:
        return None
    try:
        return float(duration)
    except (TypeError, ValueError):
        return None
