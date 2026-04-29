"""Thin wrapper over Meta's Instagram Graph API.

The Graph API only accepts the access_token as a query-string parameter
(no Authorization header support), so we never echo full request URLs in
error messages — they would leak the token. Tokens travel encrypted over
TLS to graph.facebook.com.

Auth model: long-lived user token (≈60 days, refreshable). Granted scopes
must include `instagram_basic`, `pages_show_list`,
`instagram_manage_comments`, and `business_management`. Scope errors come
back as 400 with `error.code=10` (permissions).
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import requests

from ...core.errors import AuthError, PluginError, RateLimitError


GRAPH_BASE = "https://graph.facebook.com/v19.0"


# Meta error codes that always require user intervention.
_AUTH_ERROR_CODES = {190, 102, 458, 459, 460, 463, 467}     # invalid/expired/missing token
_PERMISSION_ERROR_CODES = {10, 200, 803}                    # permissions / approval issues
# Meta uses error_subcode 2207051 for "rate limit reached", or top-level codes 4/17/32/613.
_RATE_LIMIT_CODES = {4, 17, 32, 613}


class GraphClient:
    """Read-only client for Instagram Graph API endpoints.

    Sleeps and retries 429 / 5xx with exponential backoff (2s, 4s).
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: int = 60,
        max_rate_limit_retries: int = 2,
    ):
        if not token:
            raise ValueError("Instagram access token is required")
        self.token = token
        self.timeout = timeout
        self.max_rate_limit_retries = max_rate_limit_retries
        self.session = requests.Session()

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a Graph endpoint. `path` is either '/me' or a node id like '17841…/media'."""
        delay = 2.0
        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                return self._get_once(path, params or {})
            except RateLimitError:
                if attempt >= self.max_rate_limit_retries:
                    raise
                self._sleep(delay)
                delay *= 2
        raise PluginError("retry loop exhausted")  # pragma: no cover

    def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_items: int | None = None,
    ) -> list[dict]:
        """Walk the `paging.next` chain. Returns all items collected up to max_items."""
        items: list[dict] = []
        current_path: str | None = path
        current_params: dict[str, Any] | None = dict(params or {})
        while current_path:
            data = self.get(current_path, current_params)
            page = data.get("data") or []
            for entry in page:
                items.append(entry)
                if max_items is not None and len(items) >= max_items:
                    return items
            next_url = (data.get("paging") or {}).get("next")
            if not next_url:
                break
            # The 'next' URL already contains access_token in the query; we replay it
            # verbatim through `_get_url` which strips the query and re-adds the token
            # we control (in case the embedded token is different from ours).
            current_path = self._next_path_from_url(next_url)
            current_params = self._next_params_from_url(next_url)
        return items

    # ------------------------------------------------------------------

    def _get_once(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{GRAPH_BASE}/{path.lstrip('/')}"
        # Always overwrite any embedded token.
        params = {**params, "access_token": self.token}
        try:
            r = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise PluginError(f"Network error calling Graph API: {e}") from e

        if r.status_code in (200, 201):
            try:
                return r.json()
            except ValueError as e:
                raise PluginError(f"Graph returned non-JSON: {r.text[:200]}") from e

        # Meta error envelope: {"error": {"message", "type", "code", "error_subcode"}}
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        code = err.get("code")
        message = err.get("message", r.text[:200])

        if r.status_code == 401 or code in _AUTH_ERROR_CODES:
            raise AuthError(f"Instagram Graph rejected the token (code {code}): {message}")
        if code in _PERMISSION_ERROR_CODES:
            raise AuthError(
                f"Instagram Graph permissions error (code {code}): {message}. "
                "App probably needs the instagram_basic + instagram_manage_comments scopes "
                "approved by Meta."
            )
        if r.status_code == 429 or code in _RATE_LIMIT_CODES:
            raise RateLimitError(f"Instagram Graph rate-limit (code {code}): {message}")
        if 500 <= r.status_code < 600:
            raise RateLimitError(f"Instagram Graph server error ({r.status_code}): {message}")
        raise PluginError(f"Graph {path!r} failed ({r.status_code}, code {code}): {message}")

    @staticmethod
    def _next_path_from_url(next_url: str) -> str:
        """Strip protocol/host/leading version to get just the relative path Graph expects."""
        parsed = urlparse(next_url)
        # Path looks like /v19.0/17841.../media; trim the version.
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0].startswith("v"):
            parts = parts[1:]
        return "/".join(parts)

    @staticmethod
    def _next_params_from_url(next_url: str) -> dict[str, Any]:
        from urllib.parse import parse_qs, urlparse as _u
        q = parse_qs(_u(next_url).query)
        # parse_qs returns lists; flatten singletons.
        out: dict[str, Any] = {k: (v[0] if len(v) == 1 else v) for k, v in q.items()}
        out.pop("access_token", None)  # we always inject our own
        return out
