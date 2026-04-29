"""Instagram Graph API plugin — for YOUR OWN accounts only.

Use cases this plugin solves that the public Instagram (Apify) plugin can't:
  - Insights: reach / impressions / saved / shares / plays / engagement
  - Full comment thread including replies, with likes
  - Free of charge (no Apify credits)

What you need:
  1. Convert your Instagram account to Business or Creator (instant, free).
  2. Connect it to a Facebook Page.
  3. Create a Meta Developer App: https://developers.facebook.com/apps/
  4. Generate a long-lived access token via Graph API Explorer or OAuth flow.
     Required scopes: instagram_basic, instagram_manage_comments,
     pages_show_list, business_management.
  5. Find your Instagram Business Account ID (numeric, ~17 digits).
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.redact import redact_spec
from ...core.schema import Item
from ...transcription.runner import maybe_transcribe
from .adapter import flatten_comments, media_to_item
from .client import GraphClient


# Instagram Business Account IDs are 15-20 digit numbers.
_IG_BUSINESS_ID_RE = re.compile(r"^\d{15,20}$")
# Media (post/reel) IDs are similar — long numeric strings.
_MEDIA_ID_RE = re.compile(r"^\d{10,30}(?:_\d+)?$")
# Reel-specific insight metrics; for non-Reels we use the post-grade set.
_REEL_INSIGHT_METRICS = "plays,reach,total_interactions,saved,shares"
_POST_INSIGHT_METRICS = "impressions,reach,saved"


class InstagramGraphPlugin(SourcePlugin):
    name = "instagram_graph"
    label = "Instagram (свой аккаунт)"
    secret_keys = ["INSTAGRAM_ACCESS_TOKEN"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="account",
                label="ID Business-аккаунтов",
                placeholder="17841405822304914",
                help="15-20 цифр. Найти в Meta Business Settings или через Graph API Explorer "
                     "(GET /me/accounts → instagram_business_account.id).",
            ),
            InputSpec(
                kind="post_id",
                label="ID конкретных постов/рилсов",
                placeholder="17895695668004550",
                help="ID поста (можно получить через GET /{ig-user-id}/media).",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("max_posts_per_account", "Макс. постов с аккаунта",
                      "number", 25, min_value=1, max_value=200),
            FieldSpec("fetch_comments", "Парсить комментарии", "checkbox", True),
            FieldSpec("max_comments_per_post", "Макс. комментариев на пост",
                      "number", 100, min_value=1, max_value=1000),
            FieldSpec("fetch_replies", "Включая ответы на комментарии", "checkbox", True),
            FieldSpec("fetch_insights", "🔢 Подгружать insights (reach, plays, …)",
                      "checkbox", True,
                      help="Аналитика только для своих постов. Доступна 0-90 дней после публикации."),
            FieldSpec("transcribe_videos", "🎤 Транскрибировать видео (Whisper)", "checkbox", False,
                      help="Скачивает аудио + Whisper API. Нужен OPENAI_API_KEY и ffmpeg."),
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
        for raw in inputs.get("account", []):
            v = raw.strip()
            if not _IG_BUSINESS_ID_RE.match(v):
                raise PluginError(
                    f"{raw!r} is not a valid Instagram Business Account ID "
                    "(expected 15-20 digits). Find yours via the Meta Business UI or "
                    "Graph API Explorer."
                )
            specs.append(f"account:{v}")
        for raw in inputs.get("post_id", []):
            v = raw.strip()
            if not _MEDIA_ID_RE.match(v):
                raise PluginError(
                    f"{raw!r} is not a valid Graph media ID (expected a numeric ID)."
                )
            specs.append(f"post:{v}")
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
        token = (secrets.get("INSTAGRAM_ACCESS_TOKEN") or "").strip()
        if not token:
            raise AuthError("INSTAGRAM_ACCESS_TOKEN is required")
        client = GraphClient(token)

        max_posts = int(settings.get("max_posts_per_account", 25))
        fetch_comments = bool(settings.get("fetch_comments", True))
        max_comments = int(settings.get("max_comments_per_post", 100))
        fetch_replies = bool(settings.get("fetch_replies", True))
        fetch_insights = bool(settings.get("fetch_insights", True))

        # Collect (media_dict, owner_username) pairs across all specs.
        jobs: list[tuple[dict, str | None]] = []
        for spec in item_ids:
            kind, _, value = spec.partition(":")
            try:
                self._collect_for_spec(
                    client, kind, value,
                    max_posts=max_posts,
                    jobs=jobs,
                )
            except (AuthError, PluginError):
                raise
            except Exception as e:
                raise PluginError(
                    f"Graph error for {redact_spec(spec)!r}: {e}"
                ) from e

        # Dedupe by media id.
        seen: set[str] = set()
        unique: list[tuple[dict, str | None]] = []
        for m, u in jobs:
            mid = str(m.get("id") or "")
            if mid and mid not in seen:
                seen.add(mid)
                unique.append((m, u))

        total = len(unique)
        for i, (media, owner) in enumerate(unique, 1):
            # Insights: separate call to /{media-id}/insights.
            insights_data = self._fetch_insights(client, media) if fetch_insights else None

            try:
                item = media_to_item(media, owner_username=owner, insights=insights_data)
            except Exception as e:
                item = Item(
                    source="instagram_graph",
                    item_id=str(media.get("id") or f"unknown_{i}"),
                    url=str(media.get("permalink") or ""),
                    extra={"adapter_error": str(e), "raw": media},
                )

            if fetch_comments:
                try:
                    item.comments = self._fetch_comments(
                        client, media["id"],
                        max_comments=max_comments,
                        with_replies=fetch_replies,
                    )
                except Exception as e:
                    item.extra["comments_error"] = str(e)

            maybe_transcribe(item, settings, secrets)

            if progress:
                progress(i, total, item.item_id)
            yield item

    # ------------------------------------------------------------------
    # Per-spec collection

    def _collect_for_spec(
        self,
        client: GraphClient,
        kind: str,
        value: str,
        *,
        max_posts: int,
        jobs: list[tuple[dict, str | None]],
    ) -> None:
        if kind == "account":
            user = client.get(value, params={"fields": "username"})
            owner = user.get("username")
            media_fields = (
                "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                "is_comment_enabled,comments_count,like_count,owner{id,username}"
            )
            posts = client.get_paginated(
                f"{value}/media",
                params={"fields": media_fields, "limit": min(50, max_posts)},
                max_items=max_posts,
            )
            for m in posts:
                jobs.append((m, owner))

        elif kind == "post":
            media_fields = (
                "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
                "is_comment_enabled,comments_count,like_count,owner{id,username}"
            )
            media = client.get(value, params={"fields": media_fields})
            owner = (media.get("owner") or {}).get("username")
            jobs.append((media, owner))

        else:
            raise PluginError(f"Unknown Instagram Graph input kind: {kind!r}")

    # ------------------------------------------------------------------
    # Comments

    def _fetch_comments(
        self,
        client: GraphClient,
        media_id: str,
        *,
        max_comments: int,
        with_replies: bool,
    ) -> list:
        fields = "id,text,username,timestamp,like_count,user{id}"
        if with_replies:
            fields += ",replies.limit(50){id,text,username,timestamp,like_count,user{id}}"
        comments = client.get_paginated(
            f"{media_id}/comments",
            params={"fields": fields, "limit": min(50, max_comments)},
            max_items=max_comments,
        )
        return flatten_comments(comments)

    # ------------------------------------------------------------------
    # Insights

    def _fetch_insights(self, client: GraphClient, media: dict) -> list[dict] | None:
        media_id = media.get("id")
        if not media_id:
            return None
        media_type = (media.get("media_type") or "").upper()
        # Reels use a different metric set than feed posts. Parens make the
        # precedence explicit: REEL OR (VIDEO AND product_type=REELS).
        is_reel = (media_type == "REEL") or (
            media_type == "VIDEO" and media.get("media_product_type") == "REELS"
        )
        metrics = _REEL_INSIGHT_METRICS if is_reel else _POST_INSIGHT_METRICS
        try:
            data = client.get(f"{media_id}/insights", params={"metric": metrics})
        except (AuthError, PluginError):
            # Insights commonly fail with permission errors on archived posts —
            # don't kill the whole run.
            return None
        return data.get("data") or None
