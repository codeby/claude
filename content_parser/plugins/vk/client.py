"""Thin client over the VK API (api.vk.com).

The token travels in the POST body — never as a query string — so it doesn't
leak into nginx access logs or Streamlit's URL bar history.
"""
from __future__ import annotations

from typing import Any

import requests

from ...core.errors import AuthError, PluginError, RateLimitError


VK_API_URL = "https://api.vk.com/method/{method}"
VK_API_VERSION = "5.199"


# VK error codes that mean the request will keep failing without intervention.
_AUTH_ERROR_CODES = {5, 17, 27, 28}     # bad/expired token, blocked, etc.
_RATE_LIMIT_CODES = {6, 9, 29}          # too many requests / per-second flood


class VKClient:
    def __init__(self, token: str, timeout: int = 60, version: str = VK_API_VERSION):
        if not token:
            raise ValueError("VK access token is required")
        self.token = token
        self.timeout = timeout
        self.version = version

    def call(self, method: str, **params: Any) -> Any:
        """Call a VK API method and return the unwrapped 'response' field.

        Raises AuthError, RateLimitError, or PluginError on VK-level errors.
        """
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
            r = requests.post(url, data=body, timeout=self.timeout)
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
