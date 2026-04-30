"""Convert VK API dicts into the unified core schema."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...core.schema import Comment, Item


def _iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _label_for_id(
    actor_id: int,
    profiles_by_id: dict[int, dict],
    groups_by_id: dict[int, dict],
) -> tuple[str | None, str | None]:
    """Return (display_name, full_id_string) for a VK from_id.

    VK IDs are positive for users, negative for groups.
    """
    if actor_id is None:
        return None, None
    if actor_id > 0:
        prof = profiles_by_id.get(actor_id) or {}
        first = prof.get("first_name") or ""
        last = prof.get("last_name") or ""
        name = (first + " " + last).strip() or prof.get("screen_name") or f"id{actor_id}"
        return name, f"id{actor_id}"
    if actor_id < 0:
        group = groups_by_id.get(-actor_id) or {}
        name = group.get("name") or group.get("screen_name") or f"club{-actor_id}"
        return name, f"club{-actor_id}"
    return None, None


def post_to_item(
    post: dict,
    *,
    owner_label: str | None = None,
    profiles_by_id: dict[int, dict] | None = None,
    groups_by_id: dict[int, dict] | None = None,
) -> Item:
    """Convert a VK wall post dict to core.Item.

    `owner_label` is the human-readable community/user name; if not provided,
    we look it up via groups_by_id / profiles_by_id from the same response.
    """
    profiles_by_id = profiles_by_id or {}
    groups_by_id = groups_by_id or {}

    if post.get("owner_id") is None or post.get("id") is None:
        raise ValueError(
            f"Malformed VK post: missing owner_id or id (got keys {sorted(post.keys())[:8]})"
        )
    owner_id = int(post["owner_id"])
    post_id = int(post["id"])
    item_id = f"{owner_id}_{post_id}"

    if owner_label is None:
        owner_label, _ = _label_for_id(owner_id, profiles_by_id, groups_by_id)

    text = post.get("text") or ""
    title = text.split("\n", 1)[0][:120].strip() if text else None

    likes = (post.get("likes") or {}).get("count")
    reposts = (post.get("reposts") or {}).get("count")
    views = (post.get("views") or {}).get("count")
    comments_count = (post.get("comments") or {}).get("count")

    attachments = post.get("attachments") or []
    attachment_types = [a.get("type") for a in attachments if isinstance(a, dict)]

    media: dict = {
        "views_count": views,
        "likes_count": likes,
        "reposts_count": reposts,
        "comments_count": comments_count,
        "has_photo": "photo" in attachment_types,
        "has_video": "video" in attachment_types,
        "has_link": "link" in attachment_types,
        "has_poll": "poll" in attachment_types,
        "is_pinned": bool(post.get("is_pinned")),
        "marked_as_ads": bool(post.get("marked_as_ads")),
        "post_type": post.get("post_type"),
    }
    media = {k: v for k, v in media.items() if v not in (None, False, "")}

    return Item(
        source="vk",
        item_id=item_id,
        url=f"https://vk.com/wall{item_id}",
        title=title,
        author=owner_label,
        author_id=str(owner_id) if owner_id else None,
        published_at=_iso(post.get("date")),
        text=text or None,
        media=media,
        extra={
            "attachment_types": attachment_types,
            "signer_id": post.get("signer_id"),
            "copy_history": bool(post.get("copy_history")),
        },
    )


def comment_to_core(
    c: dict,
    *,
    parent_id: str | None,
    profiles_by_id: dict[int, dict] | None = None,
    groups_by_id: dict[int, dict] | None = None,
) -> Comment:
    profiles_by_id = profiles_by_id or {}
    groups_by_id = groups_by_id or {}

    from_id = int(c.get("from_id", 0))
    author_name, author_label = _label_for_id(from_id, profiles_by_id, groups_by_id)

    return Comment(
        comment_id=str(c.get("id", "") or ""),
        parent_id=parent_id,
        author=author_name,
        author_id=author_label,
        text=c.get("text"),
        like_count=int((c.get("likes") or {}).get("count", 0) or 0),
        published_at=_iso(c.get("date")),
    )


def index_by_id(items: list[dict], key: str = "id") -> dict[int, dict]:
    """Build a dict keyed by an integer id field."""
    out: dict[int, dict] = {}
    for it in items or []:
        try:
            out[int(it[key])] = it
        except (KeyError, TypeError, ValueError):
            continue
    return out
