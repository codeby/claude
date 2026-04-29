"""Convert YouTube API dicts into the unified core schema."""
from __future__ import annotations

from ...core.schema import Comment, Item, Transcript


def metadata_to_item(meta: dict) -> Item:
    return Item(
        source="youtube",
        item_id=meta["video_id"],
        url=meta.get("url") or f"https://www.youtube.com/watch?v={meta['video_id']}",
        title=meta.get("title"),
        author=meta.get("channel_title"),
        author_id=meta.get("channel_id"),
        published_at=meta.get("published_at"),
        text=meta.get("description"),
        media={
            "duration": meta.get("duration"),
            "view_count": meta.get("view_count"),
            "like_count": meta.get("like_count"),
            "comment_count": meta.get("comment_count"),
        },
        extra={"tags": meta.get("tags", [])},
    )


def comment_dict_to_comment(c: dict) -> Comment:
    return Comment(
        comment_id=c["comment_id"],
        parent_id=c.get("parent_id"),
        author=c.get("author"),
        author_id=c.get("author_channel_id"),
        text=c.get("text"),
        like_count=c.get("like_count", 0) or 0,
        published_at=c.get("published_at"),
        updated_at=c.get("updated_at"),
    )


def transcript_dict_to_transcript(t: dict | None) -> Transcript | None:
    if not t:
        return None
    return Transcript(
        language=t.get("language"),
        is_generated=t.get("is_generated"),
        segments=t.get("segments", []),
        text=t.get("text", ""),
        error=t.get("error"),
    )
