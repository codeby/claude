"""Thin wrapper that builds a read-only praw.Reddit instance from secrets."""
from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


DEFAULT_USER_AGENT = "content_parser/1.0"


def build_reddit(secrets: dict[str, str]) -> Any:
    """Return a praw.Reddit instance configured for read-only auth.

    Imported lazily so importing this module doesn't fail when praw isn't installed.
    """
    import praw  # noqa: PLC0415

    client_id = secrets.get("REDDIT_CLIENT_ID")
    client_secret = secrets.get("REDDIT_CLIENT_SECRET")
    user_agent = secrets.get("REDDIT_USER_AGENT", "")

    if not client_id or not client_secret:
        raise ValueError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are required")

    if not user_agent.strip():
        logger.warning(
            "REDDIT_USER_AGENT not set; falling back to %r. Reddit's API rules "
            "expect '<platform>:<app-id>:<version> by /u/<username>' — generic "
            "agents may be rate-limited or blocked.",
            DEFAULT_USER_AGENT,
        )
        user_agent = DEFAULT_USER_AGENT

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )
    reddit.read_only = True
    return reddit
