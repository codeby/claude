"""Disk cache for transcription results — keyed by source + item_id.

Whisper costs money, scraping is slow; running the same job twice should
NOT re-pay for things we already transcribed. The cache lives in
~/.content_parser/transcription_cache/ as one JSON file per item.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


CACHE_DIR = Path.home() / ".content_parser" / "transcription_cache"


def _safe(value: str) -> str:
    """Sanitize a path component — collapse anything outside [\\w-] to _."""
    return re.sub(r"[^\w-]", "_", value)[:80] or "item"


def _cache_path(source: str, item_id: str) -> Path:
    return CACHE_DIR / f"{_safe(source)}_{_safe(item_id)}.json"


def get(source: str, item_id: str) -> dict | None:
    p = _cache_path(source, item_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def put(source: str, item_id: str, transcript_dict: dict) -> Path:
    """Write the transcript atomically — to a .tmp sibling, then rename.

    Without this, a crash mid-write (disk full, SIGTERM) would leave a
    truncated JSON that future `get()` calls catch as ValueError and
    treat as cache miss — but the corrupt file lingers on disk.
    """
    p = _cache_path(source, item_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(transcript_dict, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)  # atomic on POSIX; near-atomic on Windows
    return p


def clear() -> int:
    """Remove all cached transcripts. Returns count removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for p in CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            continue
    return n
