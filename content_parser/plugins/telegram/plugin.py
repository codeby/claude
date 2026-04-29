"""Telegram plugin — public channels and posts via Apify scrapers (read-only).

Uses an Apify actor (configurable, default ``apify/telegram-channel-scraper``)
because Telegram's Bot API can't read other people's groups and the user-level
MTProto API needs a phone number plus exposes the user's account to bans.
"""
from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urlparse

from ...clients.apify import ApifyClient, ApifyError
from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.redact import redact_spec
from ...core.schema import Item
from .adapter import message_to_item


_TG_HOSTS = ("t.me", "telegram.me")
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
# Apify actor IDs are <username>/<actor> or <username>~<actor>.
_ACTOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]+[/~][A-Za-z0-9_.-]+$")
_RESERVED_PATHS = {
    "joinchat", "addstickers", "share", "iv", "proxy", "socks", "addtheme",
    "login", "setlanguage", "addlist",
}


def _is_tg_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in _TG_HOSTS)


class TelegramPlugin(SourcePlugin):
    name = "telegram"
    label = "Telegram"
    secret_keys = ["APIFY_API_TOKEN"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="channel",
                label="Каналы",
                placeholder="durov\n@telegram\nhttps://t.me/somechan",
                help="Username, @handle или URL канала. Парсит последние посты.",
            ),
            InputSpec(
                kind="post_url",
                label="Ссылки на посты",
                placeholder="https://t.me/durov/123",
                help="Конкретный пост — забираем его + комментарии (если у канала есть привязанный чат).",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec(
                "actor_id", "Apify actor", "text",
                default="apify/telegram-channel-scraper",
                help="ID скрейпер-актора в Apify. По умолчанию apify/telegram-channel-scraper. "
                     "Можешь поставить другой совместимый, например 73code/telegram-scraper.",
            ),
            FieldSpec("max_messages_per_channel", "Макс. постов с канала",
                      "number", 50, min_value=1, max_value=1000),
            FieldSpec("fetch_comments", "Парсить комментарии", "checkbox", True),
            FieldSpec("max_comments_per_post", "Макс. комментариев на пост",
                      "number", 100, min_value=1, max_value=1000),
            FieldSpec("transcribe_videos", "🎤 Транскрибировать видео (Whisper)", "checkbox", False,
                      help="Скачивает аудио + шлёт в OpenAI Whisper. "
                           "Нужен OPENAI_API_KEY и ffmpeg. ~$0.006/мин."),
            FieldSpec("max_audio_seconds_per_video", "Макс. секунд аудио на пост",
                      "number", 600, min_value=10, max_value=3600),
        ]

    # ------------------------------------------------------------------
    # Resolve

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        specs: list[str] = []

        for c in inputs.get("channel", []):
            specs.append(f"channel:{self._normalize_channel(c)}")

        for u in inputs.get("post_url", []):
            url = u.strip()
            if not url:
                continue
            if self._is_private_channel_url(url):
                raise PluginError(
                    f"{url!r} is a private-channel URL (path /c/<chat_id>/...). "
                    "Apify scrapers can only read public channels."
                )
            normalized = self._extract_post_url(url)
            if not normalized:
                raise PluginError(
                    f"{url!r} doesn't look like a Telegram post URL "
                    "(expected t.me/<channel>/<message_id>)."
                )
            specs.append(f"post:{normalized}")

        return list(dict.fromkeys(specs))

    # ------------------------------------------------------------------
    # Fetch

    def fetch(
        self,
        item_ids: list[str],
        settings: dict[str, Any],
        secrets: dict[str, str],
        progress: ProgressCb | None = None,
    ) -> Iterator[Item]:
        token = secrets.get("APIFY_API_TOKEN")
        if not token:
            raise AuthError("APIFY_API_TOKEN is required")
        client = ApifyClient(token)

        actor_id = str(settings.get("actor_id") or "").strip() or "apify/telegram-channel-scraper"
        if not _ACTOR_ID_RE.match(actor_id):
            raise PluginError(
                f"Invalid actor_id {actor_id!r}. Expected 'username/actor' or 'username~actor'."
            )
        max_messages = int(settings.get("max_messages_per_channel", 50))
        fetch_comments = bool(settings.get("fetch_comments", True))
        max_comments = int(settings.get("max_comments_per_post", 100))

        # Group inputs by kind so we make one actor call per kind.
        channels: list[str] = []
        post_urls: list[str] = []
        for spec in item_ids:
            kind, _, value = spec.partition(":")
            if kind == "channel":
                channels.append(f"https://t.me/{value}")
            elif kind == "post":
                post_urls.append(value)

        all_messages: list[dict] = []

        if channels:
            try:
                all_messages.extend(
                    client.run_actor(actor_id, {
                        "urls": channels,
                        "channels": channels,
                        "directUrls": channels,
                        "maxItems": max_messages,
                        "messagesPerChannel": max_messages,
                        "maxMessages": max_messages,
                        "fetchComments": fetch_comments,
                        "extractComments": fetch_comments,
                        "commentsLimit": max_comments,
                        "maxComments": max_comments,
                    })
                )
            except ApifyError as e:
                raise PluginError(f"Apify call failed (channels): {e}") from e

        if post_urls:
            try:
                all_messages.extend(
                    client.run_actor(actor_id, {
                        "urls": post_urls,
                        "directUrls": post_urls,
                        "messageUrls": post_urls,
                        "maxItems": len(post_urls),
                        "fetchComments": fetch_comments,
                        "extractComments": fetch_comments,
                        "commentsLimit": max_comments,
                        "maxComments": max_comments,
                    })
                )
            except ApifyError as e:
                raise PluginError(f"Apify call failed (posts): {e}") from e

        # Single-pass parse + dedupe by item_id (no double work for big result sets).
        seen: set[str] = set()
        items: list[Item] = []
        for i, msg in enumerate(all_messages, 1):
            try:
                item = message_to_item(msg)
            except Exception as e:
                items.append(Item(
                    source="telegram",
                    item_id=str(msg.get("id") or f"unknown_{i}"),
                    url=str(msg.get("url") or ""),
                    extra={"adapter_error": str(e), "raw": msg},
                ))
                continue
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            items.append(item)

        total = len(items)
        for i, item in enumerate(items, 1):
            # Cap comments to settings even if the actor returned more.
            if item.comments and len(item.comments) > max_comments:
                item.comments = item.comments[:max_comments]

            from ...transcription.runner import maybe_transcribe  # noqa: PLC0415
            maybe_transcribe(item, settings, secrets)

            if progress:
                progress(i, total, item.item_id)
            yield item

    # ------------------------------------------------------------------
    # Input normalization

    @classmethod
    def _normalize_channel(cls, raw: str) -> str:
        v = raw.strip()
        if not v:
            raise PluginError("Empty channel value.")

        if v.startswith("http"):
            parsed = urlparse(v)
            if not _is_tg_host(parsed.hostname or ""):
                raise PluginError(
                    f"{raw!r} is not a Telegram URL (host must be t.me or telegram.me)."
                )
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                raise PluginError(f"Cannot parse Telegram URL: {raw!r}")
            if parts[0].lower() in _RESERVED_PATHS:
                raise PluginError(
                    f"{raw!r} is a Telegram reserved path, not a channel."
                )
            v = parts[0]

        if v.startswith("@"):
            v = v[1:]

        if not _USERNAME_RE.match(v):
            raise PluginError(
                f"{raw!r} is not a valid Telegram username "
                "(must start with a letter, 5-32 chars: letters, digits, underscore)."
            )
        return v

    @classmethod
    def _is_private_channel_url(cls, url: str) -> bool:
        """t.me/c/<chat_id>/<msg_id> is the private-channel URL form."""
        v = url.strip()
        if not v.startswith("http"):
            return False
        parsed = urlparse(v)
        if not _is_tg_host(parsed.hostname or ""):
            return False
        parts = [p for p in parsed.path.split("/") if p]
        return len(parts) >= 2 and parts[0].lower() == "c"

    @classmethod
    def _extract_post_url(cls, url: str) -> str | None:
        """Validate a t.me/<channel>/<msg_id> URL and return the canonical https form."""
        v = url.strip()
        if not v.startswith("http"):
            return None
        parsed = urlparse(v)
        if not _is_tg_host(parsed.hostname or ""):
            return None
        parts = [p for p in parsed.path.split("/") if p]
        # 'c/<chatid>/<msgid>' pattern is private channels — skip.
        if len(parts) < 2 or parts[0].lower() in _RESERVED_PATHS:
            return None
        if parts[0] == "c":
            return None
        # Last segment must be a numeric message id.
        if not parts[-1].isdigit():
            return None
        return f"https://t.me/{parts[0]}/{parts[-1]}"
