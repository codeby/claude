"""Thin client over the VK API (api.vk.com).

The token travels in the POST body — never as a query string — so it doesn't
leak into nginx access logs or Streamlit's URL bar history.
"""
from __future__ import annotations

import time
from typing import Any

import requests

from ...core.errors import AuthError, PluginError, RateLimitError


VK_API_URL = "https://api.vk.com/method/{method}"
VK_API_VERSION = "5.199"


# VK error codes that mean the request will keep failing without intervention.
_AUTH_ERROR_CODES = {5, 17, 27, 28}     # bad/expired token, blocked, etc.
_RATE_LIMIT_CODES = {6, 9, 29}          # too many requests / per-second flood


class VKClient:
    def __init__(
        self,
        token: str,
        timeout: int = 60,
        version: str = VK_API_VERSION,
        max_rate_limit_retries: int = 3,
    ):
        if not token:
            raise ValueError("VK access token is required")
        self.token = token
        self.timeout = timeout
        self.version = version
        self.max_rate_limit_retries = max_rate_limit_retries
        # Reuse one connection across requests to avoid TLS handshake per call.
        self.session = requests.Session()

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def call(self, method: str, **params: Any) -> Any:
        """Call a VK API method with retries on rate-limit errors.

        Returns the unwrapped 'response' field. Raises AuthError on bad
        token, RateLimitError after exhausting retries, PluginError on
        other API or transport errors.
        """
        delay = 1.0
        last_rate_limit: RateLimitError | None = None
        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                return self._call_once(method, params)
            except RateLimitError as e:
                last_rate_limit = e
                if attempt >= self.max_rate_limit_retries:
                    raise
                self._sleep(delay)
                delay *= 2
        # Defensive — only reachable if max_rate_limit_retries < 0.
        raise last_rate_limit  # type: ignore[misc]

    def _call_once(self, method: str, params: dict[str, Any]) -> Any:
        body = {"access_token": self.token, "v": self.version}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, bool):
                body[k] = "1" if v else "0"
            elif isinstance(v, (list, tuple)):
                body[k] = ",".join(str(x) for x in v)
            else:
                body[k] = str(v)

        url = VK_API_URL.format(method=method)
        try:
            r = self.session.post(url, data=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise PluginError(f"Network error calling VK {method}: {e}") from e

        if not r.ok:
            raise PluginError(f"VK {method} returned HTTP {r.status_code}")

        try:
            data = r.json()
        except ValueError as e:
            raise PluginError(f"VK {method} returned non-JSON: {r.text[:200]}") from e

        if "error" in data:
            err = data["error"]
            code = err.get("error_code")
            msg = err.get("error_msg", "unknown")
            if code in _AUTH_ERROR_CODES:
                raise AuthError(f"VK auth error ({code}): {msg}")
            if code in _RATE_LIMIT_CODES:
                raise RateLimitError(f"VK rate limit ({code}): {msg}")
            raise PluginError(f"VK {method} error ({code}): {msg}")

        return data.get("response")
