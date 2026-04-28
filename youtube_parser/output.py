"""Write parsed results as JSON and Markdown documents."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


def _safe_filename(name: str, max_length: int = 80) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:max_length] or "video"


def _format_seconds(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_video_json(video: dict, out_dir: Path) -> Path:
    fname = f"{video['video_id']}_{_safe_filename(video.get('title') or '')}.json"
    path = out_dir / fname
    path.write_text(json.dumps(video, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_video_markdown(video: dict, out_dir: Path) -> Path:
    fname = f"{video['video_id']}_{_safe_filename(video.get('title') or '')}.md"
    path = out_dir / fname

    lines: list[str] = []
    title = video.get("title") or video["video_id"]
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- **Channel:** {video.get('channel_title', '—')}")
    lines.append(f"- **URL:** {video.get('url')}")
    lines.append(f"- **Published:** {video.get('published_at', '—')}")
    lines.append(f"- **Duration:** {video.get('duration', '—')}")
    lines.append(
        f"- **Views / Likes / Comments:** "
        f"{video.get('view_count', '—')} / "
        f"{video.get('like_count', '—')} / "
        f"{video.get('comment_count', '—')}"
    )
    lines.append("")

    if video.get("description"):
        lines.append("## Description")
        lines.append("")
        lines.append(video["description"].strip())
        lines.append("")

    transcript = video.get("transcript")
    lines.append("## Transcript")
    lines.append("")
    if transcript and transcript.get("segments"):
        lang = transcript.get("language", "?")
        kind = "auto-generated" if transcript.get("is_generated") else "manual"
        lines.append(f"_Language: {lang} ({kind})_")
        lines.append("")
        for seg in transcript["segments"]:
            ts = _format_seconds(seg["start"])
            text = seg["text"].replace("\n", " ").strip()
            if text:
                lines.append(f"- `[{ts}]` {text}")
        lines.append("")
    else:
        lines.append("_No transcript available._")
        lines.append("")

    comments = video.get("comments") or []
    lines.append(f"## Comments ({len(comments)})")
    lines.append("")
    if not comments:
        lines.append("_No comments fetched (disabled or empty)._")
        lines.append("")
    else:
        by_parent: dict[str | None, list[dict]] = {}
        for c in comments:
            by_parent.setdefault(c.get("parent_id"), []).append(c)

        for top in by_parent.get(None, []):
            lines.append(
                f"### {top.get('author', '—')} "
                f"_({top.get('published_at', '—')}, ♥ {top.get('like_count', 0)})_"
            )
            lines.append("")
            lines.append((top.get("text") or "").strip())
            lines.append("")
            for reply in by_parent.get(top["comment_id"], []):
                lines.append(
                    f"> **{reply.get('author', '—')}** "
                    f"_({reply.get('published_at', '—')}, ♥ {reply.get('like_count', 0)})_"
                )
                lines.append("> ")
                for ln in (reply.get("text") or "").strip().splitlines():
                    lines.append(f"> {ln}")
                lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_summary_csv(videos: list[dict], out_dir: Path) -> Path:
    path = out_dir / "summary.csv"
    fields = [
        "video_id",
        "title",
        "channel_title",
        "url",
        "published_at",
        "duration",
        "view_count",
        "like_count",
        "comment_count",
        "comments_fetched",
        "transcript_language",
        "transcript_is_generated",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for v in videos:
            t = v.get("transcript") or {}
            writer.writerow(
                {
                    "video_id": v.get("video_id"),
                    "title": v.get("title"),
                    "channel_title": v.get("channel_title"),
                    "url": v.get("url"),
                    "published_at": v.get("published_at"),
                    "duration": v.get("duration"),
                    "view_count": v.get("view_count"),
                    "like_count": v.get("like_count"),
                    "comment_count": v.get("comment_count"),
                    "comments_fetched": len(v.get("comments") or []),
                    "transcript_language": t.get("language"),
                    "transcript_is_generated": t.get("is_generated"),
                }
            )
    return path


def write_combined_markdown(videos: list[dict], out_dir: Path) -> Path:
    """One big Markdown index linking to each video file."""
    path = out_dir / "index.md"
    lines = ["# YouTube Parser Results", "", f"_{len(videos)} video(s)_", ""]
    for v in videos:
        title = v.get("title") or v["video_id"]
        fname = f"{v['video_id']}_{_safe_filename(title)}.md"
        comments = len(v.get("comments") or [])
        has_transcript = bool((v.get("transcript") or {}).get("segments"))
        lines.append(
            f"- [{title}]({fname}) — {comments} comment(s), "
            f"transcript: {'yes' if has_transcript else 'no'}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
