"""Source-agnostic writers: Item → JSON / Markdown / CSV / index.

Filenames use `<source>_<item_id>_<title>` so multiple sources coexist in one folder.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from .schema import Item


_FALLBACK_FILENAME = "item"


def _safe_filename(name: str, max_length: int = 80) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:max_length] or _FALLBACK_FILENAME


def _format_seconds(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _file_stem(item: Item) -> str:
    """Build a filesystem-safe, collision-resistant stem.

    Every component goes through _safe_filename — defense in depth against an
    upstream API returning a malicious id like '../../etc/passwd'.

    If the item_id sanitizes away to the fallback (e.g. all special chars), a
    short hash of the raw (source, item_id) is appended so two such items
    don't clobber each other on disk.
    """
    safe_source = _safe_filename(item.source)
    safe_id = _safe_filename(item.item_id)
    safe_title = _safe_filename(item.title or "")

    if safe_id == _FALLBACK_FILENAME:
        digest = hashlib.sha256(
            f"{item.source}\0{item.item_id}".encode("utf-8")
        ).hexdigest()[:8]
        safe_id = f"{_FALLBACK_FILENAME}-{digest}"

    return f"{safe_source}_{safe_id}_{safe_title}"


def write_item_json(item: Item, out_dir: Path) -> Path:
    path = out_dir / f"{_file_stem(item)}.json"
    path.write_text(json.dumps(asdict(item), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_item_markdown(item: Item, out_dir: Path) -> Path:
    path = out_dir / f"{_file_stem(item)}.md"
    lines: list[str] = []

    title = item.title or item.item_id
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Source:** {item.source}")
    lines.append(f"- **Author:** {item.author or '—'}")
    lines.append(f"- **URL:** {item.url}")
    lines.append(f"- **Published:** {item.published_at or '—'}")
    if item.media:
        media_pairs = ", ".join(f"{k}={v}" for k, v in item.media.items() if v is not None)
        if media_pairs:
            lines.append(f"- **Metrics:** {media_pairs}")
    lines.append("")

    if item.text:
        lines.append("## Text")
        lines.append("")
        lines.append(item.text.strip())
        lines.append("")

    lines.append("## Transcript")
    lines.append("")
    t = item.transcript
    if t and t.segments:
        kind = "auto" if t.is_generated else "manual"
        lines.append(f"_Language: {t.language} ({kind})_")
        lines.append("")
        for seg in t.segments:
            ts = _format_seconds(seg.get("start", 0))
            text = (seg.get("text") or "").replace("\n", " ").strip()
            if text:
                lines.append(f"- `[{ts}]` {text}")
        lines.append("")
    elif t and t.error:
        lines.append(f"_Transcript error: {t.error}_")
        lines.append("")
    else:
        lines.append("_No transcript available._")
        lines.append("")

    lines.append(f"## Comments ({len(item.comments)})")
    lines.append("")
    if not item.comments:
        lines.append("_No comments._")
        lines.append("")
    else:
        by_parent: dict[str | None, list] = {}
        for c in item.comments:
            by_parent.setdefault(c.parent_id, []).append(c)

        for top in by_parent.get(None, []):
            lines.append(
                f"### {top.author or '—'} "
                f"_({top.published_at or '—'}, ♥ {top.like_count})_"
            )
            lines.append("")
            lines.append((top.text or "").strip())
            lines.append("")
            for reply in by_parent.get(top.comment_id, []):
                lines.append(
                    f"> **{reply.author or '—'}** "
                    f"_({reply.published_at or '—'}, ♥ {reply.like_count})_"
                )
                lines.append("> ")
                for ln in (reply.text or "").strip().splitlines():
                    lines.append(f"> {ln}")
                lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Defuse Excel-style formula injection in CSV cells.

    Excel/Sheets/LibreOffice treat any cell starting with =, +, -, @ as a
    formula (incl. =cmd|'/c calc'!A1). User-controlled fields like title
    and author can carry such payloads from third-party APIs. Prefixing
    with a single quote keeps the value visible but neutralizes execution.
    """
    if isinstance(value, str) and value and value[0] in _CSV_INJECTION_PREFIXES:
        return "'" + value
    return value


def write_summary_csv(items: list[Item], out_dir: Path) -> Path:
    path = out_dir / "summary.csv"
    metric_keys: set[str] = set()
    for it in items:
        metric_keys.update(it.media.keys())
    metric_keys_sorted = sorted(metric_keys)

    fields = [
        "source", "item_id", "title", "author", "url",
        "published_at", "comments_fetched",
        "transcript_language", "transcript_is_generated",
    ] + metric_keys_sorted

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for it in items:
            row: dict = {
                "source": _csv_safe(it.source),
                "item_id": _csv_safe(it.item_id),
                "title": _csv_safe(it.title),
                "author": _csv_safe(it.author),
                "url": _csv_safe(it.url),
                "published_at": _csv_safe(it.published_at),
                "comments_fetched": len(it.comments),
                "transcript_language": _csv_safe(it.transcript.language if it.transcript else None),
                "transcript_is_generated": it.transcript.is_generated if it.transcript else None,
            }
            for k in metric_keys_sorted:
                row[k] = _csv_safe(it.media.get(k))
            writer.writerow(row)
    return path


def write_index_markdown(items: list[Item], out_dir: Path) -> Path:
    path = out_dir / "index.md"
    lines = [f"# Results ({len(items)} item(s))", ""]
    for it in items:
        title = it.title or it.item_id
        fname = f"{_file_stem(it)}.md"
        comments = len(it.comments)
        has_t = bool(it.transcript and it.transcript.segments)
        lines.append(
            f"- [{title}]({fname}) — `{it.source}`, "
            f"{comments} comment(s), transcript: {'yes' if has_t else 'no'}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
