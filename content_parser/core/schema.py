"""Source-agnostic data model returned by every plugin."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Comment:
    comment_id: str
    parent_id: str | None = None
    author: str | None = None
    author_id: str | None = None
    text: str | None = None
    like_count: int = 0
    published_at: str | None = None
    updated_at: str | None = None


@dataclass
class Transcript:
    language: str | None = None
    is_generated: bool | None = None
    segments: list[dict] = field(default_factory=list)
    text: str = ""
    error: str | None = None


@dataclass
class Item:
    source: str
    item_id: str
    url: str
    title: str | None = None
    author: str | None = None
    author_id: str | None = None
    published_at: str | None = None
    text: str | None = None
    media: dict[str, Any] = field(default_factory=dict)
    transcript: Transcript | None = None
    comments: list[Comment] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
