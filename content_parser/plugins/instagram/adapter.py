"""Convert Apify Instagram-scraper output dicts into the unified core schema."""
from __future__ import annotations

from ...core.schema import Comment, Item


def _comment_from_apify(c: dict, parent_id: str | None = None) -> Comment:
    return Comment(
        comment_id=str(c.get("id") or c.get("comment_id") or ""),
        parent_id=parent_id,
        author=c.get("ownerUsername") or c.get("owner_username"),
        author_id=str(c.get("ownerId") or c.get("owner_id") or "") or None,
        text=c.get("text"),
        like_count=int(c.get("likesCount", 0) or 0),
        published_at=c.get("timestamp"),
    )


def _flatten_comments(raw: list[dict]) -> list[Comment]:
    out: list[Comment] = []
    for c in raw or []:
        top = _comment_from_apify(c)
        out.append(top)
        for reply in c.get("replies", []) or []:
            out.append(_comment_from_apify(reply, parent_id=top.comment_id))
    return out


def post_to_item(post: dict) -> Item:
    short_code = post.get("shortCode") or post.get("shortcode") or post.get("id", "")
    url = post.get("url") or (
        f"https://www.instagram.com/p/{short_code}/" if short_code else ""
    )

    music = post.get("musicInfo") or {}
    media: dict = {
        "type": post.get("type"),                        # Image, Video, Sidecar
        "view_count": post.get("videoViewCount") or post.get("videoPlayCount"),
        "like_count": post.get("likesCount"),
        "comment_count": post.get("commentsCount"),
        "video_url": post.get("videoUrl"),
        "display_url": post.get("displayUrl"),
        "video_duration": post.get("videoDuration"),
        "audio_id": music.get("audio_id") or music.get("audioId"),
        "audio_artist": music.get("artist_name") or music.get("artistName"),
        "audio_title": music.get("song_name") or music.get("songName"),
    }
    media = {k: v for k, v in media.items() if v is not None}

    return Item(
        source="instagram",
        item_id=short_code or post.get("id", ""),
        url=url,
        title=(post.get("caption") or "").split("\n", 1)[0][:120] or None,
        author=post.get("ownerUsername") or post.get("owner_username"),
        author_id=str(post.get("ownerId") or "") or None,
        published_at=post.get("timestamp"),
        text=post.get("caption"),
        media=media,
        comments=_flatten_comments(post.get("latestComments") or post.get("comments") or []),
        extra={
            "hashtags": post.get("hashtags", []),
            "mentions": post.get("mentions", []),
            "location_name": post.get("locationName"),
            "is_sponsored": post.get("isSponsored"),
            "product_type": post.get("productType"),
        },
    )
