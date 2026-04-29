"""VK plugin — communities, walls, and comments via VK API (read-only)."""
from __future__ import annotations

import logging
import re
from typing import Any, Iterator
from urllib.parse import urlparse

from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from .adapter import (
    comment_to_core,
    index_by_id,
    post_to_item,
)
from .client import VKClient


logger = logging.getLogger(__name__)


_VK_HOSTS = ("vk.com", "vk.ru", "m.vk.com")
_SCREEN_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,32}$")
# Wall post URL: vk.com/wall<owner>_<post>  (owner can be -group or +user)
_WALL_RE = re.compile(r"^wall(-?\d+)_(\d+)$")
# Common VK reserved-namespace prefixes that aren't communities/users.
_RESERVED_PATH_PREFIXES = {
    "wall", "feed", "video", "audio", "doc", "photo", "id", "club", "public",
    "event", "topic", "albums", "market", "im", "friends", "settings",
    "search", "groups", "apps", "stickers", "support", "dev",
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


def _is_vk_host(host: str) -> bool:
    host = (host or "").lower()
    return any(host == h or host.endswith("." + h) for h in _VK_HOSTS)


class VKPlugin(SourcePlugin):
    name = "vk"
    label = "ВКонтакте"
    secret_keys = ["VK_ACCESS_TOKEN"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="query",
                label="Поиск сообществ",
                placeholder="маркетинг\nрилсы для бизнеса",
                help="Полнотекстовый поиск по сообществам ВК. Парсит стены найденных групп.",
            ),
            InputSpec(
                kind="community",
                label="Сообщества",
                placeholder="durov_says\nhttps://vk.com/club1\n12345",
                help="Screen name, club-ID или URL сообщества.",
            ),
            InputSpec(
                kind="post_url",
                label="Ссылки на посты",
                placeholder="https://vk.com/wall-12345_678",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("max_communities_per_query", "Макс. сообществ на поисковый запрос",
                      "number", 10, min_value=1, max_value=100),
            FieldSpec("max_posts_per_input", "Макс. постов с сообщества/из поиска",
                      "number", 25, min_value=1, max_value=100,
                      help="Лимит VK API на wall.get — 100 постов за вызов."),
            FieldSpec("fetch_comments", "Парсить комментарии", "checkbox", True),
            FieldSpec("max_comments_per_post", "Макс. комментариев на пост",
                      "number", 100, min_value=1, max_value=1000),
            FieldSpec("comment_depth", "Глубина комментариев", "select", "top_level",
                      options=["top_level", "all"],
                      help="top_level — только верхний уровень; all — со всеми ответами."),
        ]

    # ------------------------------------------------------------------
    # Resolve

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        """Returns 'kind:value' specs so fetch() routes them through VK API."""
        specs: list[str] = []

        for q in inputs.get("query", []):
            q = q.strip()
            if q:
                specs.append(f"query:{q}")

        for c in inputs.get("community", []):
            specs.append(f"community:{self._normalize_community(c)}")

        for url in inputs.get("post_url", []):
            url = url.strip()
            if not url:
                continue
            wall_id = self._extract_wall_id(url)
            if not wall_id:
                raise PluginError(
                    f"{url!r} doesn't look like a VK post URL "
                    "(expected vk.com/wall-<group_id>_<post_id>)."
                )
            specs.append(f"post:{wall_id}")

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
        token = secrets.get("VK_ACCESS_TOKEN")
        if not token:
            raise AuthError("VK_ACCESS_TOKEN is required")
        client = VKClient(token)

        max_communities = int(settings.get("max_communities_per_query", 10))
        max_posts = min(int(settings.get("max_posts_per_input", 25)), 100)
        fetch_comments = bool(settings.get("fetch_comments", True))
        max_comments = int(settings.get("max_comments_per_post", 100))
        depth = str(settings.get("comment_depth", "top_level"))

        # Step 1: resolve all specs into a list of (post_dict, owner_label)
        # plus a cache of group/profile dicts for author resolution in comments.
        post_jobs: list[tuple[dict, str | None]] = []  # (post, owner_label)
        groups_cache: dict[int, dict] = {}
        profiles_cache: dict[int, dict] = {}

        for spec in item_ids:
            kind, _, value = spec.partition(":")
            try:
                self._collect_for_spec(
                    client, kind, value,
                    max_communities=max_communities,
                    max_posts=max_posts,
                    post_jobs=post_jobs,
                    groups_cache=groups_cache,
                    profiles_cache=profiles_cache,
                )
            except (AuthError, PluginError):
                raise
            except Exception as e:
                raise PluginError(
                    f"VK error for {_redact_spec(spec)!r}: {e}"
                ) from e

        # Dedupe by VK item_id (owner_post)
        seen: set[str] = set()
        unique_jobs: list[tuple[dict, str | None]] = []
        for post, owner_label in post_jobs:
            owner_id = post.get("owner_id")
            post_id = post.get("id")
            iid = f"{owner_id}_{post_id}"
            if iid not in seen:
                seen.add(iid)
                unique_jobs.append((post, owner_label))

        # Step 2: yield items, optionally with comments.
        total = len(unique_jobs)
        for i, (post, owner_label) in enumerate(unique_jobs, 1):
            item = post_to_item(
                post,
                owner_label=owner_label,
                profiles_by_id=profiles_cache,
                groups_by_id=groups_cache,
            )
            if fetch_comments:
                try:
                    item.comments = self._fetch_comments(
                        client,
                        owner_id=int(post["owner_id"]),
                        post_id=int(post["id"]),
                        max_comments=max_comments,
                        depth=depth,
                    )
                except Exception as e:
                    item.extra["comments_error"] = str(e)

            if progress:
                progress(i, total, item.item_id)
            yield item

    # ------------------------------------------------------------------
    # Per-spec collection

    def _collect_for_spec(
        self,
        client: VKClient,
        kind: str,
        value: str,
        *,
        max_communities: int,
        max_posts: int,
        post_jobs: list[tuple[dict, str | None]],
        groups_cache: dict[int, dict],
        profiles_cache: dict[int, dict],
    ) -> None:
        if kind == "query":
            search_resp = client.call("groups.search", q=value, count=max_communities)
            groups = (search_resp or {}).get("items", [])
            for g in groups:
                groups_cache[int(g["id"])] = g
                self._collect_wall(client, -int(g["id"]), g.get("name"), max_posts, post_jobs, profiles_cache, groups_cache)

        elif kind == "community":
            owner_id, label = self._resolve_community(client, value, groups_cache, profiles_cache)
            self._collect_wall(client, owner_id, label, max_posts, post_jobs, profiles_cache, groups_cache)

        elif kind == "post":
            posts_resp = client.call("wall.getById", posts=value, extended=1)
            if isinstance(posts_resp, dict):
                items = posts_resp.get("items", [])
                for p in posts_resp.get("profiles", []) or []:
                    profiles_cache[int(p["id"])] = p
                for g in posts_resp.get("groups", []) or []:
                    groups_cache[int(g["id"])] = g
            else:
                items = posts_resp or []
            for post in items:
                owner_id = int(post.get("owner_id", 0))
                if owner_id < 0:
                    g = groups_cache.get(-owner_id) or {}
                    label = g.get("name")
                else:
                    p = profiles_cache.get(owner_id) or {}
                    label = ((p.get("first_name") or "") + " " + (p.get("last_name") or "")).strip() or None
                post_jobs.append((post, label))

        else:
            raise PluginError(f"Unknown VK input kind: {kind!r}")

    def _collect_wall(
        self,
        client: VKClient,
        owner_id: int,
        owner_label: str | None,
        max_posts: int,
        post_jobs: list[tuple[dict, str | None]],
        profiles_cache: dict[int, dict],
        groups_cache: dict[int, dict],
    ) -> None:
        resp = client.call("wall.get", owner_id=owner_id, count=max_posts, extended=1)
        if isinstance(resp, dict):
            for p in resp.get("profiles", []) or []:
                profiles_cache[int(p["id"])] = p
            for g in resp.get("groups", []) or []:
                groups_cache[int(g["id"])] = g
            items = resp.get("items", [])
        else:
            items = resp or []
        for post in items:
            post_jobs.append((post, owner_label))

    # ------------------------------------------------------------------
    # Comments

    def _fetch_comments(
        self,
        client: VKClient,
        *,
        owner_id: int,
        post_id: int,
        max_comments: int,
        depth: str,
    ) -> list:
        # VK caps `count` at 100 per request — paginate if needed.
        out: list = []
        offset = 0
        page = min(100, max_comments)
        thread_count = 0 if depth == "top_level" else 10

        while len(out) < max_comments:
            resp = client.call(
                "wall.getComments",
                owner_id=owner_id,
                post_id=post_id,
                offset=offset,
                count=page,
                need_likes=1,
                extended=1,
                thread_items_count=thread_count,
                sort="asc",
            )
            if not isinstance(resp, dict):
                break
            items = resp.get("items", [])
            profiles = index_by_id(resp.get("profiles") or [])
            groups = index_by_id(resp.get("groups") or [])

            for c in items:
                out.append(comment_to_core(c, parent_id=None, profiles_by_id=profiles, groups_by_id=groups))
                if len(out) >= max_comments:
                    break
                if depth == "all":
                    thread = c.get("thread") or {}
                    for reply in thread.get("items", []) or []:
                        out.append(
                            comment_to_core(
                                reply,
                                parent_id=str(c.get("id", "") or ""),
                                profiles_by_id=profiles,
                                groups_by_id=groups,
                            )
                        )
                        if len(out) >= max_comments:
                            break

            if not items or len(items) < page:
                break
            offset += len(items)

        return out

    # ------------------------------------------------------------------
    # Community resolution

    def _resolve_community(
        self,
        client: VKClient,
        value: str,
        groups_cache: dict[int, dict],
        profiles_cache: dict[int, dict],
    ) -> tuple[int, str | None]:
        """Return (owner_id, label) for a community spec.

        owner_id is negative for groups; positive for users (rare for
        community input but handled because VK treats them the same way).
        """
        resp = client.call("groups.getById", group_ids=value, fields="name,screen_name")
        if isinstance(resp, dict):
            items = resp.get("groups") or []
        else:
            items = resp or []
        if not items:
            raise PluginError(f"VK community {value!r} not found")
        g = items[0]
        gid = int(g["id"])
        groups_cache[gid] = g
        return -gid, g.get("name")

    # ------------------------------------------------------------------
    # Input normalization

    @classmethod
    def _normalize_community(cls, raw: str) -> str:
        """Return a value usable in groups.getById: screen_name, 'club<id>', or numeric id."""
        v = raw.strip()
        if not v:
            raise PluginError("Empty community value.")

        if v.startswith("http"):
            parsed = urlparse(v)
            if not _is_vk_host(parsed.hostname or ""):
                raise PluginError(
                    f"{raw!r} is not a VK URL (host must be vk.com)."
                )
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                raise PluginError(f"Cannot parse community URL: {raw!r}")
            v = parts[0]

        # 'club12345', 'public12345' — VK community URL prefixes
        m = re.match(r"^(?:club|public)(\d+)$", v, re.IGNORECASE)
        if m:
            return f"club{m.group(1)}"

        if v.isdigit():
            return v

        if v.lower() in _RESERVED_PATH_PREFIXES:
            raise PluginError(
                f"{raw!r} is a VK reserved path, not a community."
            )

        if not _SCREEN_NAME_RE.match(v):
            raise PluginError(
                f"{raw!r} is not a valid VK community identifier "
                "(letters, digits, underscore, dot; 1-32 chars)."
            )
        return v

    @classmethod
    def _extract_wall_id(cls, url: str) -> str | None:
        """Return 'owner_post' string for a vk.com/wall... URL, else None."""
        v = url.strip()
        if v.startswith("http"):
            parsed = urlparse(v)
            if not _is_vk_host(parsed.hostname or ""):
                return None
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                return None
            v = parts[0]
        m = _WALL_RE.match(v)
        if not m:
            return None
        return f"{m.group(1)}_{m.group(2)}"
