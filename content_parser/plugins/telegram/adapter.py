"""Convert Apify Telegram-scraper output dicts into the unified core schema.

Field names vary between scraper actors, so the adapter looks up each
logical field through a list of likely keys (`_pick`) and falls back to None.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...core.schema import Comment, Item


def _pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _iso(value: Any) -> str | None:
    """Accepts ISO string, UNIX timestamp, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _reactions_total(reactions: Any) -> int | None:
    """reactions can be:
        [{"emoji": "👍", "count": 5}, ...] → sum counts
        {"👍": 5, "❤️": 2}                  → sum values
        an int                               → as-is
    """
    if reactions is None:
        return None
    if isinstance(reactions, int):
        return reactions
    if isinstance(reactions, list):
        total = 0
        for r in reactions:
            if isinstance(r, dict):
                try:
                    total += int(r.get("count", 0) or 0)
                except (TypeError, ValueError):
                    continue
        return total or None
    if isinstance(reactions, dict):
        try:
            return sum(int(v or 0) for v in reactions.values())
        except (TypeError, ValueError):
            return None
    return None


def _channel_label(msg: dict) -> tuple[str | None, str | None]:
    """Extract (display name, channel username) from a message dict."""
    title = _pick(msg, "channelTitle", "channel_title", "chatTitle", "chat_title")
    username = _pick(msg, "channelUsername", "channel_username", "chatUsername", "chat_username")
    if not title and not username:
        # nested 'channel' or 'chat' subobject
        for k in ("channel", "chat"):
            sub = msg.get(k)
            if isinstance(sub, dict):
                title = title or _pick(sub, "title", "name")
                username = username or _pick(sub, "username", "screen_name")
    return title, username


def message_to_item(msg: dict) -> Item:
    """Convert a Telegram message dict (from any common Apify actor) to core.Item."""
    msg_id = _pick(msg, "id", "messageId", "message_id")
    if msg_id is None:
        raise ValueError(
            f"Malformed Telegram message: missing id (got keys {sorted(msg.keys())[:8]})"
        )

    title_label, username = _channel_label(msg)
    url = _pick(msg, "url", "messageUrl", "message_url")
    if not url and username and msg_id is not None:
        url = f"https://t.me/{username}/{msg_id}"

    item_id = f"{username}_{msg_id}" if username else str(msg_id)

    text = _pick(msg, "text", "message", "content", default="")

    reactions_count = _reactions_total(_pick(msg, "reactions", "reactions_count"))
    media_obj = msg.get("media") if isinstance(msg.get("media"), dict) else None
    media_type = _pick(msg, "mediaType", "media_type") or (
        media_obj.get("type") if media_obj else None
    )

    media: dict = {
        "views_count": _pick(msg, "views", "viewCount", "view_count"),
        "forwards_count": _pick(msg, "forwards", "forwardCount", "forward_count"),
        # Numeric replies count if present; otherwise the length of any embedded comments list.
        "comments_count": _pick(msg, "repliesCount", "replies_count", "commentsCount"),
        "reactions_count": reactions_count,
        "media_type": media_type,
        "is_pinned": bool(_pick(msg, "isPinned", "is_pinned", default=False)),
    }
    # Drop None/empty/False but keep zero counts (0 is a meaningful signal).
    media = {k: v for k, v in media.items() if v is not None and v != "" and v is not False}

    title = text.split("\n", 1)[0][:120].strip() if text else None

    return Item(
        source="telegram",
        item_id=item_id,
        url=url or "",
        title=title,
        author=title_label or username,
        author_id=username,
        published_at=_iso(_pick(msg, "date", "timestamp", "publishedAt", "published_at")),
        text=text or None,
        media=media,
        comments=_extract_comments(msg, parent_url=url),
        extra={
            "channel_id": _pick(msg, "channelId", "channel_id", "chatId", "chat_id"),
            "is_forwarded": bool(_pick(msg, "fwdFromId", "forwarded_from", default=False)),
            "has_media": bool(msg.get("media")),
        },
    )


def _extract_comments(msg: dict, *, parent_url: str | None = None) -> list[Comment]:
    raw = _pick(msg, "replies_data", "comments", "discussion", "thread", default=None)
    if raw is None:
        return []
    # 'replies' might be a count int OR a list of comment dicts depending on actor
    if isinstance(raw, int):
        return []
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]
    if not isinstance(raw, list):
        return []

    out: list[Comment] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        out.append(_comment_from_dict(c, parent_id=None))
    return out


def _comment_from_dict(c: dict, *, parent_id: str | None) -> Comment:
    cid = _pick(c, "id", "messageId", "message_id", "comment_id")
    sender = c.get("from") or c.get("sender") or {}
    author_name = (
        _pick(c, "authorName", "author_name", "fromName")
        or (isinstance(sender, dict) and (sender.get("name") or
            ((sender.get("first_name") or "") + " " + (sender.get("last_name") or "")).strip()))
        or (isinstance(sender, dict) and sender.get("username"))
        or _pick(c, "username", "fromUsername")
        or None
    )
    if author_name == "":
        author_name = None
    author_id = (
        _pick(c, "authorUsername", "author_username", "fromUsername")
        or (isinstance(sender, dict) and sender.get("username"))
        or None
    )

    return Comment(
        comment_id=str(cid if cid is not None else ""),
        parent_id=parent_id,
        author=author_name,
        author_id=author_id,
        text=_pick(c, "text", "message", "content"),
        like_count=int(_reactions_total(_pick(c, "reactions", "reactions_count")) or 0),
        published_at=_iso(_pick(c, "date", "timestamp", "publishedAt", "published_at")),
    )
