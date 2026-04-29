"""Instagram plugin — posts and reels via Apify's instagram-scraper actor."""
from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import urlparse

from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from .adapter import post_to_item
from .apify_client import ApifyClient, ApifyError


ACTOR_ID = "apify/instagram-scraper"

# Path segments that indicate a URL refers to a post/reel rather than an account.
_POST_PATH_SEGMENTS = {"p", "reel", "reels", "tv", "explore", "stories"}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


class InstagramPlugin(SourcePlugin):
    name = "instagram"
    label = "Instagram"
    secret_keys = ["APIFY_API_TOKEN"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="hashtag",
                label="Хэштеги",
                placeholder="smm\nреклама\nmarketing",
                help="Без #. Каждая строка — один тег.",
            ),
            InputSpec(
                kind="account",
                label="Аккаунты",
                placeholder="nasa\n@durov\nhttps://instagram.com/zuck",
                help="Только username, @handle или URL профиля. Ссылки на /p/ или /reel/ — во вкладку «Ссылки на посты/рилсы».",
            ),
            InputSpec(
                kind="post_url",
                label="Ссылки на посты/рилсы",
                placeholder="https://www.instagram.com/p/xxxxx/\nhttps://www.instagram.com/reel/yyyyy/",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("max_posts_per_input", "Макс. постов на источник", "number", 20,
                      min_value=1, max_value=500,
                      help="Apify тарифицируется за пост — большие значения дороже."),
            FieldSpec("results_type", "Тип данных для аккаунтов/хэштегов",
                      "select", "posts",
                      options=["posts", "details", "comments"],
                      help="Для прямых ссылок на посты/рилсы всегда используется 'details'."),
            FieldSpec("add_parent_data", "Включать данные родительского аккаунта", "checkbox", False),
            FieldSpec("transcribe_videos", "🎤 Транскрибировать видео (Whisper)", "checkbox", False,
                      help="Скачивает аудио рилса и шлёт в OpenAI Whisper. "
                           "Нужен OPENAI_API_KEY и ffmpeg на машине. ~$0.006/мин."),
            FieldSpec("max_audio_seconds_per_video", "Макс. секунд аудио на пост",
                      "number", 600, min_value=10, max_value=3600,
                      help="Если рилс длиннее — пропускается. Защита от случайных счетов."),
        ]

    # ------------------------------------------------------------------
    # Resolve

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        """Returns 'kind:url' specs so fetch() can route each to the right Apify call."""
        specs: list[str] = []
        for h in inputs.get("hashtag", []):
            tag = h.strip().lstrip("#")
            if tag:
                specs.append(f"hashtag:https://www.instagram.com/explore/tags/{tag}/")

        for a in inputs.get("account", []):
            user = self._normalize_account(a)
            specs.append(f"account:https://www.instagram.com/{user}/")

        for u in inputs.get("post_url", []):
            url = u.strip()
            if not url:
                continue
            if not self._is_post_url(url):
                raise PluginError(
                    f"{url!r} doesn't look like a post or reel URL "
                    "(expected /p/ or /reel/ in the path)."
                )
            specs.append(f"post:{url}")

        # Dedupe preserving order.
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

        # Group inputs by kind so each goes to the right Apify resultsType.
        groups: dict[str, list[str]] = {"hashtag": [], "account": [], "post": []}
        for spec in item_ids:
            kind, _, url = spec.partition(":")
            if kind in groups and url:
                groups[kind].append(url)

        # Aggregate "what to ask Apify": per (resultsType, urls) call.
        listing_type = str(settings.get("results_type", "posts"))
        listing_urls = groups["hashtag"] + groups["account"]
        post_urls = groups["post"]

        max_posts = int(settings.get("max_posts_per_input", 20))
        add_parent = bool(settings.get("add_parent_data", False))

        # Apify run #1: hashtags + accounts → user-chosen results_type
        all_posts: list[dict] = []
        if listing_urls:
            try:
                all_posts.extend(
                    client.run_actor(ACTOR_ID, {
                        "directUrls": listing_urls,
                        "resultsType": listing_type,
                        "resultsLimit": max_posts,
                        "addParentData": add_parent,
                    })
                )
            except ApifyError as e:
                raise PluginError(f"Apify call failed (listing): {e}") from e

        # Apify run #2: explicit post URLs → always 'details'
        if post_urls:
            try:
                all_posts.extend(
                    client.run_actor(ACTOR_ID, {
                        "directUrls": post_urls,
                        "resultsType": "details",
                        "resultsLimit": max(len(post_urls), 1),
                        "addParentData": add_parent,
                    })
                )
            except ApifyError as e:
                raise PluginError(f"Apify call failed (post details): {e}") from e

        total = len(all_posts)
        for i, post in enumerate(all_posts, 1):
            try:
                item = post_to_item(post)
            except Exception as e:
                item = Item(
                    source="instagram",
                    item_id=str(post.get("shortCode") or post.get("id") or f"unknown_{i}"),
                    url=str(post.get("url") or ""),
                    extra={"adapter_error": str(e), "raw": post},
                )

            from ...transcription.runner import maybe_transcribe  # noqa: PLC0415
            maybe_transcribe(item, settings, secrets)

            if progress:
                progress(i, total, item.item_id)
            yield item

    # ------------------------------------------------------------------
    # Helpers

    @staticmethod
    def _is_post_url(url: str) -> bool:
        try:
            parts = [p for p in urlparse(url).path.split("/") if p]
        except Exception:
            return False
        return bool(parts) and parts[0] in _POST_PATH_SEGMENTS

    @classmethod
    def _normalize_account(cls, value: str) -> str:
        v = value.strip().lstrip("@")
        if v.startswith("http"):
            parsed = urlparse(v)
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                raise PluginError(f"Cannot parse account URL: {value!r}")
            head = parts[0]
            if head in _POST_PATH_SEGMENTS:
                raise PluginError(
                    f"{value!r} is a post/reel URL, not an account. "
                    "Use the «Ссылки на посты/рилсы» tab."
                )
            v = head
        if not _USERNAME_RE.match(v):
            raise PluginError(
                f"{value!r} is not a valid Instagram username "
                "(letters, digits, dot, underscore; 1-30 chars)."
            )
        return v
