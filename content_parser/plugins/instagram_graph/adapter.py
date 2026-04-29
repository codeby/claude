"""Convert Instagram Graph API responses into the unified core schema."""
from __future__ import annotations

from typing import Any

from ...core.schema import Comment, Item


def media_to_item(media: dict, *, owner_username: str | None = None) -> Item:
    """Convert a Graph API media object (post / reel / carousel) to core.Item.

    Comments are NOT populated here — the plugin attaches them after a
    separate /{media-id}/comments call to keep paging explicit.
    """
    if not media.get("id"):
        raise ValueError(
            f"Malformed Graph media: missing id (got keys {sorted(media.keys())[:6]})"
        )

    media_type = media.get("media_type")  # IMAGE / VIDEO / CAROUSEL_ALBUM / REEL
    insights = _flatten_insights(media.get("insights"))

    media_dict: dict[str, Any] = {
        "media_type": media_type,
        "like_count": media.get("like_count"),
        "comments_count": media.get("comments_count"),
        "is_comment_enabled": media.get("is_comment_enabled"),
        "media_url": media.get("media_url"),
        "thumbnail_url": media.get("thumbnail_url"),
        "permalink": media.get("permalink"),
        # Reel/post insight metrics, when fetched.
        "plays": insights.get("plays"),
        "reach": insights.get("reach"),
        "impressions": insights.get("impressions"),
        "saved": insights.get("saved"),
        "shares": insights.get("shares"),
        "total_interactions": insights.get("total_interactions"),
        "video_views": insights.get("video_views"),
    }
    media_dict = {k: v for k, v in media_dict.items() if v not in (None, "")}

    caption = media.get("caption") or ""
    title = caption.split("\n", 1)[0][:120].strip() if caption else None

    return Item(
        source="instagram_graph",
        item_id=str(media["id"]),
        url=media.get("permalink") or "",
        title=title,
        author=owner_username,
        author_id=str(media.get("owner", {}).get("id") or media.get("owner_id") or "") or None,
        published_at=media.get("timestamp"),
        text=caption or None,
        media=media_dict,
        extra={
            "shortcode": media.get("shortcode"),
            "is_shared_to_feed": media.get("is_shared_to_feed"),
            "children": media.get("children", {}).get("data") if media.get("children") else None,
        },
    )


def comment_to_core(c: dict, parent_id: str | None = None) -> Comment:
    return Comment(
        comment_id=str(c.get("id", "") or ""),
        parent_id=parent_id,
        author=c.get("username"),
        author_id=str(c.get("user", {}).get("id") or "") or None,
        text=c.get("text"),
        like_count=int(c.get("like_count", 0) or 0),
        published_at=c.get("timestamp"),
    )


def flatten_comments(comments: list[dict]) -> list[Comment]:
    """Each top-level comment may have a `replies.data` list expanded inline."""
    out: list[Comment] = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        top = comment_to_core(c)
        out.append(top)
        replies = ((c.get("replies") or {}).get("data")) or []
        for reply in replies:
            if isinstance(reply, dict):
                out.append(comment_to_core(reply, parent_id=top.comment_id))
    return out


# ----------------------------------------------------------------------
# Helpers


def _flatten_insights(insights: Any) -> dict[str, int | float]:
    """Graph returns insights as {"data": [{"name": "reach", "values": [{"value": 1234}]}, ...]}.

    We flatten to {name: value}.
    """
    if not insights:
        return {}
    if isinstance(insights, dict):
        data = insights.get("data") or []
    elif isinstance(insights, list):
        data = insights
    else:
        return {}

    out: dict[str, int | float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name:
            continue
        values = entry.get("values") or []
        if values and isinstance(values[0], dict):
            v = values[0].get("value")
            if isinstance(v, (int, float)):
                out[name] = v
    return out
