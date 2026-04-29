"""Telegram plugin — public channels and posts via Apify scrapers (read-only).

Uses an Apify actor (configurable, default ``apify/telegram-channel-scraper``)
because Telegram's Bot API can't read other people's groups and the user-level
MTProto API needs a phone number plus exposes the user's account to bans.
"""
from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urlparse

from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from ..instagram.apify_client import ApifyClient, ApifyError
from .adapter import message_to_item


_TG_HOSTS = ("t.me", "telegram.me")
_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_RESERVED_PATHS = {
    "joinchat", "addstickers", "share", "iv", "proxy", "socks", "addtheme",
    "login", "setlanguage", "addlist",
}


def _redact_spec(spec: str) -> str:
    """Trim spec for safe logging — drop query/fragment, cap to 80 chars."""
    for sep in ("?", "#"):
        if sep in spec:
            spec = spec.split(sep, 1)[0] + sep + "…"
            break
    if len(spec) > 80:
        spec = spec[:77] + "…"
    return spec


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

        actor_id = str(settings.get("actor_id") or "apify/telegram-channel-scraper").strip()
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

        # Dedupe by item_id (channel_username + message id).
        seen: set[str] = set()
        unique: list[dict] = []
        for msg in all_messages:
            try:
                item = message_to_item(msg)
            except (ValueError, KeyError):
                continue
            if item.item_id in seen:
                continue
            seen.add(item.item_id)
            unique.append(msg)

        total = len(unique)
        for i, msg in enumerate(unique, 1):
            try:
                item = message_to_item(msg)
            except Exception as e:
                item = Item(
                    source="telegram",
                    item_id=str(msg.get("id") or f"unknown_{i}"),
                    url=str(msg.get("url") or ""),
                    extra={"adapter_error": str(e), "raw": msg},
                )

            # Cap comments to settings even if the actor returned more.
            if item.comments and len(item.comments) > max_comments:
                item.comments = item.comments[:max_comments]

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
