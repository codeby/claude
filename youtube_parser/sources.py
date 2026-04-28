"""Resolve search queries, channel URLs, and playlist URLs to lists of video IDs."""
from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse

from googleapiclient.discovery import Resource


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
PLAYLIST_ID_RE = re.compile(r"^(PL|UU|FL|RD|OL|LL)[A-Za-z0-9_-]+$")


def extract_video_id(url_or_id: str) -> str | None:
    if VIDEO_ID_RE.match(url_or_id):
        return url_or_id
    parsed = urlparse(url_or_id)
    if parsed.hostname in ("youtu.be",):
        vid = parsed.path.lstrip("/")
        return vid if VIDEO_ID_RE.match(vid) else None
    if parsed.hostname and "youtube" in parsed.hostname:
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [None])[0]
            return vid if vid and VIDEO_ID_RE.match(vid) else None
        if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            vid = parsed.path.split("/")[2]
            return vid if VIDEO_ID_RE.match(vid) else None
    return None


def extract_playlist_id(url_or_id: str) -> str | None:
    if PLAYLIST_ID_RE.match(url_or_id):
        return url_or_id
    parsed = urlparse(url_or_id)
    pid = parse_qs(parsed.query).get("list", [None])[0]
    return pid if pid and PLAYLIST_ID_RE.match(pid) else None


def resolve_channel_id(youtube: Resource, channel_input: str) -> str:
    """Accepts a channel ID, /channel/UC..., /@handle, /c/name, or /user/name URL."""
    if CHANNEL_ID_RE.match(channel_input):
        return channel_input

    parsed = urlparse(channel_input)
    path = parsed.path.strip("/") if parsed.path else channel_input.lstrip("@")
    parts = path.split("/")

    if parts and parts[0] == "channel" and len(parts) > 1 and CHANNEL_ID_RE.match(parts[1]):
        return parts[1]

    handle = None
    username = None
    if parts and parts[0].startswith("@"):
        handle = parts[0]
    elif parts and parts[0] == "c" and len(parts) > 1:
        handle = "@" + parts[1]
    elif parts and parts[0] == "user" and len(parts) > 1:
        username = parts[1]
    elif channel_input.startswith("@"):
        handle = channel_input

    request_kwargs: dict = {"part": "id"}
    if handle:
        request_kwargs["forHandle"] = handle
    elif username:
        request_kwargs["forUsername"] = username
    else:
        raise ValueError(f"Cannot resolve channel from input: {channel_input!r}")

    response = youtube.channels().list(**request_kwargs).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found: {channel_input!r}")
    return items[0]["id"]


def get_uploads_playlist_id(youtube: Resource, channel_id: str) -> str:
    response = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_playlist_video_ids(
    youtube: Resource, playlist_id: str, max_results: int | None = None
) -> list[str]:
    video_ids: list[str] = []
    page_token: str | None = None
    while True:
        response = (
            youtube.playlistItems()
            .list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            vid = item["contentDetails"]["videoId"]
            video_ids.append(vid)
            if max_results and len(video_ids) >= max_results:
                return video_ids
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def search_video_ids(youtube: Resource, query: str, max_results: int = 25) -> list[str]:
    """Uses search.list (100 quota units per call). Each call returns up to 50 results."""
    video_ids: list[str] = []
    page_token: str | None = None
    while len(video_ids) < max_results:
        page_size = min(50, max_results - len(video_ids))
        response = (
            youtube.search()
            .list(
                part="id",
                q=query,
                type="video",
                maxResults=page_size,
                pageToken=page_token,
            )
            .execute()
        )
        for item in response.get("items", []):
            vid = item["id"].get("videoId")
            if vid:
                video_ids.append(vid)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def collect_video_ids(
    youtube: Resource,
    *,
    queries: Iterable[str] = (),
    channels: Iterable[str] = (),
    playlists: Iterable[str] = (),
    videos: Iterable[str] = (),
    search_max: int = 25,
    per_source_max: int | None = None,
) -> list[str]:
    """Resolve all inputs to a deduplicated, ordered list of video IDs."""
    seen: set[str] = set()
    result: list[str] = []

    def add(vid: str) -> None:
        if vid and vid not in seen:
            seen.add(vid)
            result.append(vid)

    for q in queries:
        for vid in search_video_ids(youtube, q, max_results=search_max):
            add(vid)

    for ch in channels:
        channel_id = resolve_channel_id(youtube, ch)
        uploads_id = get_uploads_playlist_id(youtube, channel_id)
        for vid in list_playlist_video_ids(youtube, uploads_id, max_results=per_source_max):
            add(vid)

    for pl in playlists:
        playlist_id = extract_playlist_id(pl)
        if not playlist_id:
            raise ValueError(f"Cannot parse playlist input: {pl!r}")
        for vid in list_playlist_video_ids(youtube, playlist_id, max_results=per_source_max):
            add(vid)

    for v in videos:
        vid = extract_video_id(v)
        if not vid:
            raise ValueError(f"Cannot parse video input: {v!r}")
        add(vid)

    return result


def fetch_video_metadata(youtube: Resource, video_ids: list[str]) -> dict[str, dict]:
    """Returns a dict mapping video_id -> metadata. Batches up to 50 IDs per call."""
    result: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        response = (
            youtube.videos()
            .list(part="snippet,statistics,contentDetails", id=",".join(batch))
            .execute()
        )
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            details = item.get("contentDetails", {})
            result[item["id"]] = {
                "video_id": item["id"],
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("channelTitle"),
                "published_at": snippet.get("publishedAt"),
                "tags": snippet.get("tags", []),
                "duration": details.get("duration"),
                "view_count": int(stats.get("viewCount", 0)) if stats.get("viewCount") else None,
                "like_count": int(stats.get("likeCount", 0)) if stats.get("likeCount") else None,
                "comment_count": int(stats.get("commentCount", 0)) if stats.get("commentCount") else None,
                "url": f"https://www.youtube.com/watch?v={item['id']}",
            }
    return result
