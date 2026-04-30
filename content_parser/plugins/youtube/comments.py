"""Fetch comments (and optionally replies) for a video via YouTube Data API."""
from __future__ import annotations

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError


def _format_comment(snippet: dict, comment_id: str, parent_id: str | None = None) -> dict:
    return {
        "comment_id": comment_id,
        "parent_id": parent_id,
        "author": snippet.get("authorDisplayName"),
        "author_channel_id": (snippet.get("authorChannelId") or {}).get("value"),
        "text": snippet.get("textOriginal") or snippet.get("textDisplay"),
        "like_count": snippet.get("likeCount", 0),
        "published_at": snippet.get("publishedAt"),
        "updated_at": snippet.get("updatedAt"),
    }


def fetch_comments(
    youtube: Resource,
    video_id: str,
    *,
    include_replies: bool = False,
    max_comments: int | None = None,
    order: str = "relevance",
) -> list[dict]:
    """Fetch top-level comments. If include_replies=True, also pull all replies."""
    comments: list[dict] = []
    page_token: str | None = None

    while True:
        try:
            response = (
                youtube.commentThreads()
                .list(
                    part="snippet,replies" if include_replies else "snippet",
                    videoId=video_id,
                    maxResults=100,
                    order=order,
                    pageToken=page_token,
                    textFormat="plainText",
                )
                .execute()
            )
        except HttpError as e:
            if e.resp.status == 403 and b"commentsDisabled" in e.content:
                return []
            raise

        for item in response.get("items", []):
            top_snippet = item["snippet"]["topLevelComment"]["snippet"]
            top_id = item["snippet"]["topLevelComment"]["id"]
            comments.append(_format_comment(top_snippet, top_id))

            if max_comments and len(comments) >= max_comments:
                return comments

            if include_replies:
                # Bound the reply pull by remaining cap so a single popular
                # top-level comment with hundreds of replies doesn't burn quota
                # only for the slice [:max_comments] to throw most of them away.
                remaining = (max_comments - len(comments)) if max_comments else None
                reply_count = item["snippet"].get("totalReplyCount", 0)
                inline_replies = item.get("replies", {}).get("comments", [])
                if reply_count and len(inline_replies) < reply_count:
                    comments.extend(
                        _fetch_all_replies(youtube, top_id, max_replies=remaining)
                    )
                else:
                    for reply in inline_replies:
                        if remaining is not None and remaining <= 0:
                            break
                        comments.append(
                            _format_comment(reply["snippet"], reply["id"], parent_id=top_id)
                        )
                        if remaining is not None:
                            remaining -= 1
                if max_comments and len(comments) >= max_comments:
                    return comments[:max_comments]

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return comments


def _fetch_all_replies(
    youtube: Resource,
    parent_id: str,
    *,
    max_replies: int | None = None,
) -> list[dict]:
    replies: list[dict] = []
    page_token: str | None = None
    while True:
        if max_replies is not None and len(replies) >= max_replies:
            return replies[:max_replies]
        page_size = 100 if max_replies is None else min(100, max_replies - len(replies))
        if page_size <= 0:
            return replies
        response = (
            youtube.comments()
            .list(
                part="snippet",
                parentId=parent_id,
                maxResults=page_size,
                pageToken=page_token,
                textFormat="plainText",
            )
            .execute()
        )
        for item in response.get("items", []):
            replies.append(_format_comment(item["snippet"], item["id"], parent_id=parent_id))
            if max_replies is not None and len(replies) >= max_replies:
                return replies[:max_replies]
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return replies
