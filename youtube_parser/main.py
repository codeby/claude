"""CLI entry point: parse comments and transcripts for YouTube videos."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from googleapiclient.discovery import build

from .comments import fetch_comments
from .output import (
    write_combined_markdown,
    write_summary_csv,
    write_video_json,
    write_video_markdown,
)
from .sources import collect_video_ids, fetch_video_metadata
from .transcripts import fetch_transcript_verbose


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="youtube_parser",
        description="Parse comments and transcripts from YouTube videos.",
    )
    parser.add_argument(
        "--query", "-q", action="append", default=[],
        help="Search query (can be passed multiple times). Costs 100 quota units per call.",
    )
    parser.add_argument(
        "--channel", "-c", action="append", default=[],
        help="Channel URL, @handle, or channel ID (can be repeated).",
    )
    parser.add_argument(
        "--playlist", "-p", action="append", default=[],
        help="Playlist URL or ID (can be repeated).",
    )
    parser.add_argument(
        "--video", "-v", action="append", default=[],
        help="Video URL or ID (can be repeated).",
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("YOUTUBE_API_KEY"),
        help="YouTube Data API v3 key. Defaults to $YOUTUBE_API_KEY.",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output directory. Defaults to ./output/<timestamp>/.",
    )
    parser.add_argument(
        "--search-max", type=int, default=25,
        help="Max videos per --query (default: 25).",
    )
    parser.add_argument(
        "--per-source-max", type=int, default=None,
        help="Max videos per channel/playlist (default: unlimited).",
    )
    parser.add_argument(
        "--max-comments", type=int, default=None,
        help="Max comments per video (default: all).",
    )
    parser.add_argument(
        "--include-replies", action="store_true",
        help="Also fetch replies to top-level comments.",
    )
    parser.add_argument(
        "--comment-order", choices=("relevance", "time"), default="relevance",
        help="Order of comments (default: relevance).",
    )
    parser.add_argument(
        "--transcript-langs", default="ru,en",
        help="Preferred transcript languages, comma-separated (default: ru,en).",
    )
    parser.add_argument(
        "--no-transcripts", action="store_true",
        help="Skip transcript fetching.",
    )
    parser.add_argument(
        "--no-comments", action="store_true",
        help="Skip comment fetching.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (args.query or args.channel or args.playlist or args.video):
        print("Provide at least one of --query / --channel / --playlist / --video.", file=sys.stderr)
        return 2

    if not args.api_key:
        print("Missing API key: pass --api-key or set $YOUTUBE_API_KEY.", file=sys.stderr)
        return 2

    out_dir = Path(args.output) if args.output else Path("output") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {out_dir.resolve()}")

    youtube = build("youtube", "v3", developerKey=args.api_key, cache_discovery=False)

    print("Resolving inputs to video IDs...")
    video_ids = collect_video_ids(
        youtube,
        queries=args.query,
        channels=args.channel,
        playlists=args.playlist,
        videos=args.video,
        search_max=args.search_max,
        per_source_max=args.per_source_max,
    )
    if not video_ids:
        print("No videos resolved.", file=sys.stderr)
        return 1
    print(f"Found {len(video_ids)} unique video(s).")

    print("Fetching video metadata...")
    metadata = fetch_video_metadata(youtube, video_ids)

    languages = [s.strip() for s in args.transcript_langs.split(",") if s.strip()]
    results: list[dict] = []

    for i, vid in enumerate(video_ids, 1):
        meta = metadata.get(vid)
        if not meta:
            print(f"  [{i}/{len(video_ids)}] {vid}: metadata unavailable, skipping")
            continue

        print(f"  [{i}/{len(video_ids)}] {vid}: {meta['title'][:70] if meta.get('title') else ''}")

        comments: list[dict] = []
        if not args.no_comments:
            try:
                comments = fetch_comments(
                    youtube, vid,
                    include_replies=args.include_replies,
                    max_comments=args.max_comments,
                    order=args.comment_order,
                )
                print(f"    comments: {len(comments)}")
            except Exception as e:
                print(f"    comments error: {e}")

        transcript = None
        if not args.no_transcripts:
            t = fetch_transcript_verbose(vid, languages=languages)
            if t.get("segments"):
                print(
                    f"    transcript: {t['language']} "
                    f"({'auto' if t['is_generated'] else 'manual'}, "
                    f"{len(t['segments'])} segments)"
                )
                transcript = {
                    "language": t["language"],
                    "is_generated": t["is_generated"],
                    "segments": t["segments"],
                    "text": t["text"],
                }
            else:
                print(f"    transcript: not available ({t.get('error') or 'unknown'})")

        record = dict(meta)
        record["comments"] = comments
        record["transcript"] = transcript
        results.append(record)

        write_video_json(record, out_dir)
        write_video_markdown(record, out_dir)

    write_summary_csv(results, out_dir)
    write_combined_markdown(results, out_dir)

    print(f"\nDone. {len(results)} video(s) saved to {out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
