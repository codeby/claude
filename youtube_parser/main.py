"""Back-compat CLI: translates legacy flags into 'content_parser.cli run --source youtube'.

The original argument set is preserved so existing scripts keep working.
"""
from __future__ import annotations

import argparse
import os
import sys

from content_parser.cli import main as cp_main


def _build_legacy_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="youtube_parser")
    p.add_argument("--query", "-q", action="append", default=[])
    p.add_argument("--channel", "-c", action="append", default=[])
    p.add_argument("--playlist", "-p", action="append", default=[])
    p.add_argument("--video", "-v", action="append", default=[])
    p.add_argument("--api-key", default=os.environ.get("YOUTUBE_API_KEY"))
    p.add_argument("--output", "-o", default=None)
    p.add_argument("--search-max", type=int, default=25)
    p.add_argument("--per-source-max", type=int, default=None)
    p.add_argument("--max-comments", type=int, default=None)
    p.add_argument("--include-replies", action="store_true")
    p.add_argument("--comment-order", choices=("relevance", "time"), default="relevance")
    p.add_argument("--transcript-langs", default="ru,en")
    p.add_argument("--no-transcripts", action="store_true")
    p.add_argument("--no-comments", action="store_true")
    return p


def _to_new_argv(args: argparse.Namespace) -> list[str]:
    new = ["run", "--source", "youtube"]

    if args.output:
        new += ["--output", args.output]

    for q in args.query:
        new += ["--query", q]
    for c in args.channel:
        new += ["--channel", c]
    for pl in args.playlist:
        new += ["--playlist", pl]
    for v in args.video:
        new += ["--video", v]

    settings: dict[str, str] = {
        "search_max": str(args.search_max),
        "per_source_max": str(args.per_source_max if args.per_source_max is not None else 0),
        "max_comments": str(args.max_comments if args.max_comments is not None else 0),
        "include_replies": "true" if args.include_replies else "false",
        "comment_order": args.comment_order,
        "transcript_langs": args.transcript_langs,
        "fetch_transcripts": "false" if args.no_transcripts else "true",
        "fetch_comments": "false" if args.no_comments else "true",
    }
    for k, v in settings.items():
        new += ["--set", f"{k}={v}"]
    return new


def main(argv: list[str] | None = None) -> int:
    args = _build_legacy_parser().parse_args(argv)

    if not (args.query or args.channel or args.playlist or args.video):
        print("Provide at least one of --query / --channel / --playlist / --video.", file=sys.stderr)
        return 2

    if args.api_key:
        os.environ.setdefault("YOUTUBE_API_KEY", args.api_key)

    return cp_main(_to_new_argv(args))


if __name__ == "__main__":
    sys.exit(main())
