"""Reddit plugin — posts and comments via PRAW (read-only)."""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Iterator
from urllib.parse import urlparse

from ...core.errors import AuthError, PluginError
from ...core.plugin import FieldSpec, InputSpec, ProgressCb, SourcePlugin
from ...core.schema import Item
from .adapter import comment_to_core, submission_to_item
from .client import build_reddit


logger = logging.getLogger(__name__)


_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,21}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
_REDDIT_HOSTS = ("reddit.com", "redd.it")
# Hard cap on MoreComments expansions when expand_more=True. Each expansion
# triggers a network round-trip and can pull ~250 extra comments, so an
# unbounded replace_more(limit=None) easily produces minutes of work and
# Reddit-side rate limits on big threads.
_MAX_REPLACE_MORE = 32


def _redact_spec(spec: str) -> str:
    """Trim a spec for safe logging — drop query/fragment, cap to 80 chars.

    A user might paste a URL with a token in the query (?token=...) or fragment
    (#access_token=...); neither belongs in logs or exception messages.
    """
    for sep in ("?", "#"):
        if sep in spec:
            spec = spec.split(sep, 1)[0] + sep + "…"
            break
    if len(spec) > 80:
        spec = spec[:77] + "…"
    return spec


def _is_reddit_host(host: str) -> bool:
    host = host.lower()
    return any(host == h or host.endswith("." + h) for h in _REDDIT_HOSTS)


class RedditPlugin(SourcePlugin):
    name = "reddit"
    label = "Reddit"
    secret_keys = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"]

    def input_specs(self) -> list[InputSpec]:
        return [
            InputSpec(
                kind="subreddit",
                label="Сабреддиты",
                placeholder="python\nMachineLearning\nhttps://reddit.com/r/marketing",
                help="Имя саба, с/без префикса r/, или URL.",
            ),
            InputSpec(
                kind="query",
                label="Поисковые запросы",
                placeholder="claude code\nremote work",
                help="Полнотекстовый поиск по всему Reddit.",
            ),
            InputSpec(
                kind="post_url",
                label="Ссылки на посты",
                placeholder="https://www.reddit.com/r/python/comments/abc123/title/",
            ),
            InputSpec(
                kind="user",
                label="Авторы",
                placeholder="spez\n/u/automoderator",
                help="Username для трекинга постов конкретного пользователя.",
            ),
        ]

    def settings_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("listing", "Сортировка постов", "select", "top",
                      options=["hot", "top", "new", "rising", "controversial"]),
            FieldSpec("time_filter", "Период (для top/controversial)", "select", "month",
                      options=["hour", "day", "week", "month", "year", "all"]),
            FieldSpec("max_posts_per_input", "Макс. постов на источник", "number", 25,
                      min_value=1, max_value=500),
            FieldSpec("fetch_comments", "Парсить комментарии", "checkbox", True),
            FieldSpec("max_comments_per_post", "Макс. комментариев на пост", "number", 100,
                      min_value=1, max_value=2000),
            FieldSpec("comment_depth", "Глубина комментариев", "select", "top_level",
                      options=["top_level", "all"],
                      help="top_level — только верхний уровень; all — все включая ответы."),
            FieldSpec("expand_more_comments", "Раскрывать «load more» (медленно)", "checkbox", False),
        ]

    # ------------------------------------------------------------------
    # Resolve

    def resolve(
        self,
        inputs: dict[str, list[str]],
        settings: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[str]:
        """Returns 'kind:value' specs so fetch() routes them through PRAW."""
        specs: list[str] = []

        for raw in inputs.get("subreddit", []):
            name = self._normalize_subreddit(raw)
            specs.append(f"subreddit:{name}")

        for q in inputs.get("query", []):
            q = q.strip()
            if q:
                specs.append(f"query:{q}")

        for url in inputs.get("post_url", []):
            url = url.strip()
            if not url:
                continue
            if not self._is_reddit_post_url(url):
                raise PluginError(
                    f"{url!r} doesn't look like a Reddit post URL "
                    "(expected /r/<sub>/comments/<id>/)."
                )
            specs.append(f"post_url:{url}")

        for u in inputs.get("user", []):
            name = self._normalize_user(u)
            specs.append(f"user:{name}")

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
        if not secrets.get("REDDIT_CLIENT_ID") or not secrets.get("REDDIT_CLIENT_SECRET"):
            raise AuthError("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are required")

        reddit = build_reddit(secrets)

        listing = str(settings.get("listing", "top"))
        time_filter = str(settings.get("time_filter", "month"))
        max_posts = int(settings.get("max_posts_per_input", 25))
        fetch_comments = bool(settings.get("fetch_comments", True))
        max_comments = int(settings.get("max_comments_per_post", 100))
        depth = str(settings.get("comment_depth", "top_level"))
        expand_more = bool(settings.get("expand_more_comments", False))

        # Collect all submissions across specs, then yield with comments attached.
        submissions: list[Any] = []
        for spec in item_ids:
            kind, _, value = spec.partition(":")
            try:
                submissions.extend(self._collect_submissions(
                    reddit, kind, value, listing, time_filter, max_posts
                ))
            except Exception as e:
                raise PluginError(
                    f"Reddit error for {_redact_spec(spec)!r}: {e}"
                ) from e

        # Dedupe by submission id (same post can come from multiple inputs).
        seen: set[str] = set()
        unique_subs: list[Any] = []
        for s in submissions:
            sid = str(getattr(s, "id", "") or "")
            if sid and sid not in seen:
                seen.add(sid)
                unique_subs.append(s)

        total = len(unique_subs)
        for i, sub in enumerate(unique_subs, 1):
            item = submission_to_item(sub)

            if fetch_comments:
                try:
                    item.comments = self._collect_comments(
                        sub, max_comments=max_comments, depth=depth, expand_more=expand_more
                    )
                except Exception as e:
                    item.extra["comments_error"] = str(e)

            if progress:
                progress(i, total, item.item_id)
            yield item

    # ------------------------------------------------------------------
    # Helpers — submission collection per input kind

    def _collect_submissions(
        self,
        reddit: Any,
        kind: str,
        value: str,
        listing: str,
        time_filter: str,
        limit: int,
    ) -> Iterable[Any]:
        if kind == "subreddit":
            sub = reddit.subreddit(value)
            return self._listing_iter(sub, listing, time_filter, limit)

        if kind == "query":
            sub = reddit.subreddit("all")
            return list(sub.search(value, sort=self._search_sort(listing), time_filter=time_filter, limit=limit))

        if kind == "user":
            redditor = reddit.redditor(value)
            return self._listing_iter(redditor.submissions, listing, time_filter, limit, is_user=True)

        if kind == "post_url":
            return [reddit.submission(url=value)]

        raise PluginError(f"Unknown Reddit input kind: {kind!r}")

    @staticmethod
    def _listing_iter(
        source: Any, listing: str, time_filter: str, limit: int, is_user: bool = False
    ) -> list[Any]:
        # User submissions has slightly different listing methods.
        if listing == "top":
            return list(source.top(time_filter=time_filter, limit=limit))
        if listing == "controversial":
            return list(source.controversial(time_filter=time_filter, limit=limit))
        if listing == "new":
            return list(source.new(limit=limit))
        if listing == "rising":
            if is_user:
                logger.info(
                    "Reddit user submissions don't expose 'rising'; using 'new' instead."
                )
                return list(source.new(limit=limit))
            return list(source.rising(limit=limit))
        # default 'hot'
        return list(source.hot(limit=limit))

    @staticmethod
    def _search_sort(listing: str) -> str:
        # Reddit search sort: relevance, hot, top, new, comments
        if listing in ("top", "new", "hot", "comments"):
            return listing
        return "relevance"

    # ------------------------------------------------------------------
    # Comment flattening

    @staticmethod
    def _collect_comments(
        sub: Any, *, max_comments: int, depth: str, expand_more: bool
    ) -> list:
        # When expand_more=True, cap at _MAX_REPLACE_MORE expansions instead of
        # unbounded — see the constant for the rationale.
        sub.comments.replace_more(limit=_MAX_REPLACE_MORE if expand_more else 0)

        out: list = []

        def _walk(comments_iter, parent_id: str | None, only_top: bool) -> None:
            for c in comments_iter:
                if len(out) >= max_comments:
                    return
                # MoreComments may slip through if expand_more=False and replace_more left some
                if c.__class__.__name__ == "MoreComments":
                    continue
                out.append(comment_to_core(c, parent_id))
                if only_top:
                    continue
                replies = getattr(c, "replies", None)
                if replies:
                    _walk(replies, str(getattr(c, "id", "") or ""), only_top=False)

        if depth == "top_level":
            _walk(sub.comments, parent_id=None, only_top=True)
        else:
            _walk(sub.comments, parent_id=None, only_top=False)

        return out

    # ------------------------------------------------------------------
    # Input normalization

    @classmethod
    def _normalize_subreddit(cls, raw: str) -> str:
        v = raw.strip()
        if v.startswith("http"):
            parsed = urlparse(v)
            if not _is_reddit_host(parsed.hostname or ""):
                raise PluginError(
                    f"{raw!r} is not a Reddit URL (host must be reddit.com or redd.it)."
                )
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0].lower() == "r":
                v = parts[1]
            else:
                raise PluginError(f"Cannot parse subreddit URL: {raw!r}")
        if v.lower().startswith("r/"):
            v = v[2:]
        v = v.strip("/")
        if not _SUBREDDIT_RE.match(v):
            raise PluginError(
                f"{raw!r} is not a valid subreddit name "
                "(letters, digits, underscore; 1-21 chars)."
            )
        return v

    @classmethod
    def _normalize_user(cls, raw: str) -> str:
        v = raw.strip()
        if v.startswith("http"):
            parsed = urlparse(v)
            if not _is_reddit_host(parsed.hostname or ""):
                raise PluginError(
                    f"{raw!r} is not a Reddit URL (host must be reddit.com or redd.it)."
                )
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0].lower() in ("u", "user"):
                v = parts[1]
            else:
                raise PluginError(f"Cannot parse user URL: {raw!r}")
        # Note: the longer prefixes ('/user/', '/u/') must be checked before the
        # shorter ones ('user/', 'u/'), and '@' last, so we don't strip too little.
        for prefix in ("/user/", "/u/", "user/", "u/", "@"):
            if v.lower().startswith(prefix):
                v = v[len(prefix):]
                break
        v = v.strip("/")
        if not _USERNAME_RE.match(v):
            raise PluginError(
                f"{raw!r} is not a valid Reddit username "
                "(letters, digits, _ or -; 3-20 chars)."
            )
        return v

    @staticmethod
    def _is_reddit_post_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
            parts = [p for p in parsed.path.split("/") if p]
        except Exception:
            return False
        if not _is_reddit_host(parsed.hostname or ""):
            return False
        # Expected: /r/<sub>/comments/<id>/<slug>/
        return len(parts) >= 4 and parts[0].lower() == "r" and parts[2].lower() == "comments"
