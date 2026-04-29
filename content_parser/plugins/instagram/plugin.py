"""Instagram plugin — posts and reels via Apify's instagram-scraper actor."""
from __future__ import annotations

from typing import Any, Iterator
from urllib.parse import urlparse

from ...core.errors import AuthError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from .adapter import post_to_item
from .apify_client import ApifyClient, ApifyError


ACTOR_ID = "apify/instagram-scraper"


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
                help="Без #. Каждая строка = один тег.",
            ),
            InputSpec(
                kind="account",
                label="Аккаунты",
                placeholder="nasa\n@durov\nhttps://instagram.com/zuck",
                help="Username, @handle или полный URL профиля.",
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
            FieldSpec("results_type", "Тип данных", "select", "posts",
                      options=["posts", "details", "comments"],
                      help="posts: последние посты; details: детали + комменты; comments: только комменты к посту."),
            FieldSpec("add_parent_data", "Включать данные родительского аккаунта", "checkbox", False),
        ]

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        # Each spec becomes one direct URL fed to the actor. We dedupe but don't yet hit Apify.
        specs: list[str] = []
        for h in inputs.get("hashtag", []):
            tag = h.strip().lstrip("#")
            if tag:
                specs.append(f"https://www.instagram.com/explore/tags/{tag}/")
        for a in inputs.get("account", []):
            user = self._normalize_account(a)
            if user:
                specs.append(f"https://www.instagram.com/{user}/")
        for u in inputs.get("post_url", []):
            url = u.strip()
            if url:
                specs.append(url)
        return list(dict.fromkeys(specs))

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

        actor_input = {
            "directUrls": item_ids,
            "resultsType": settings.get("results_type", "posts"),
            "resultsLimit": int(settings.get("max_posts_per_input", 20)),
            "addParentData": bool(settings.get("add_parent_data", False)),
        }

        try:
            posts = client.run_actor(ACTOR_ID, actor_input)
        except ApifyError as e:
            raise AuthError(str(e)) from e

        total = len(posts)
        for i, post in enumerate(posts, 1):
            try:
                item = post_to_item(post)
            except Exception as e:
                item = Item(
                    source="instagram",
                    item_id=str(post.get("shortCode") or post.get("id") or f"unknown_{i}"),
                    url=str(post.get("url") or ""),
                    extra={"adapter_error": str(e), "raw": post},
                )
            if progress:
                progress(i, total, item.item_id)
            yield item

    @staticmethod
    def _normalize_account(value: str) -> str:
        v = value.strip().lstrip("@")
        if v.startswith("http"):
            parsed = urlparse(v)
            parts = [p for p in parsed.path.split("/") if p]
            if parts:
                return parts[0]
        return v
