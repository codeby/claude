"""Thin wrapper that builds a read-only praw.Reddit instance from secrets."""
from __future__ import annotations

from typing import Any


DEFAULT_USER_AGENT = "content_parser/1.0"


def build_reddit(secrets: dict[str, str]) -> Any:
    """Return a praw.Reddit instance configured for read-only auth.

    Imported lazily so importing this module doesn't fail when praw isn't installed.
    """
    import praw  # noqa: PLC0415

    client_id = secrets.get("REDDIT_CLIENT_ID")
    client_secret = secrets.get("REDDIT_CLIENT_SECRET")
    user_agent = secrets.get("REDDIT_USER_AGENT") or DEFAULT_USER_AGENT

    if not client_id or not client_secret:
        raise ValueError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are required")

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True
    return reddit
