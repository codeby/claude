"""Convert PRAW Submission/Comment objects into the unified core schema."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...core.schema import Comment, Item


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _author_str(author: Any) -> str | None:
    """PRAW returns a Redditor object, None for [deleted], or sometimes a string."""
    if author is None:
        return "[deleted]"
    name = getattr(author, "name", None)
    if name:
        return str(name)
    return str(author) if author else "[deleted]"


def _subreddit_str(subreddit: Any) -> str | None:
    if subreddit is None:
        return None
    return getattr(subreddit, "display_name", None) or str(subreddit)


def submission_to_item(s: Any) -> Item:
    """Map a PRAW Submission to core.Item.

    Comments are NOT populated here — the plugin attaches them separately
    after iterating s.comments to keep ordering and depth control explicit.
    """
    permalink = getattr(s, "permalink", "") or ""
    url_external = getattr(s, "url", None)
    is_self = bool(getattr(s, "is_self", False))

    media: dict = {
        "score": getattr(s, "score", None),
        "upvote_ratio": getattr(s, "upvote_ratio", None),
        "num_comments": getattr(s, "num_comments", None),
        "num_crossposts": getattr(s, "num_crossposts", None),
        "subreddit": _subreddit_str(getattr(s, "subreddit", None)),
        "flair": getattr(s, "link_flair_text", None),
        "is_video": bool(getattr(s, "is_video", False)),
        "is_self": is_self,
        "over_18": bool(getattr(s, "over_18", False)),
        "spoiler": bool(getattr(s, "spoiler", False)),
        "locked": bool(getattr(s, "locked", False)),
        "domain": getattr(s, "domain", None),
        "url_external": None if is_self else url_external,
    }
    media = {k: v for k, v in media.items() if v not in (None, "")}

    extra: dict = {}
    awards = getattr(s, "all_awardings", None)
    if awards:
        extra["awards"] = [
            {"name": a.get("name"), "count": a.get("count")} for a in awards if isinstance(a, dict)
        ]
    post_hint = getattr(s, "post_hint", None)
    if post_hint:
        extra["post_hint"] = post_hint
    removed = getattr(s, "removed_by_category", None)
    if removed:
        extra["removed_by_category"] = removed

    return Item(
        source="reddit",
        item_id=str(getattr(s, "id", "") or ""),
        url=f"https://reddit.com{permalink}" if permalink else (url_external or ""),
        title=getattr(s, "title", None),
        author=_author_str(getattr(s, "author", None)),
        author_id=getattr(s, "author_fullname", None),
        published_at=_iso(getattr(s, "created_utc", None)),
        text=(getattr(s, "selftext", None) or None) if is_self else None,
        media=media,
        extra=extra,
    )


def comment_to_core(c: Any, parent_id: str | None) -> Comment:
    return Comment(
        comment_id=str(getattr(c, "id", "") or ""),
        parent_id=parent_id,
        author=_author_str(getattr(c, "author", None)),
        author_id=getattr(c, "author_fullname", None),
        text=getattr(c, "body", None),
        like_count=int(getattr(c, "score", 0) or 0),
        published_at=_iso(getattr(c, "created_utc", None)),
    )
