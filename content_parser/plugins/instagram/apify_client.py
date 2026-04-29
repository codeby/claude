"""Minimal Apify HTTP client — runs an actor synchronously and returns dataset items."""
from __future__ import annotations

from typing import Any

import requests


APIFY_BASE = "https://api.apify.com/v2"


class ApifyError(Exception):
    pass


class ApifyClient:
    def __init__(self, token: str, timeout: int = 600):
        if not token:
            raise ValueError("Apify token is required")
        self.token = token
        self.timeout = timeout

    def run_actor(
        self,
        actor_id: str,
        actor_input: dict[str, Any],
    ) -> list[dict]:
        """Run an actor synchronously and return its dataset items.

        actor_id: e.g. "apify/instagram-scraper" — slashes get replaced by '~'.
        """
        slug = actor_id.replace("/", "~")
        url = f"{APIFY_BASE}/acts/{slug}/run-sync-get-dataset-items"
        params = {"token": self.token, "format": "json"}
        try:
            r = requests.post(url, params=params, json=actor_input, timeout=self.timeout)
        except requests.RequestException as e:
            raise ApifyError(f"Network error talking to Apify: {e}") from e

        if r.status_code == 401:
            raise ApifyError("Apify rejected the token (401). Check APIFY_API_TOKEN.")
        if r.status_code == 402:
            raise ApifyError("Apify says the account is out of credits (402).")
        if not r.ok:
            raise ApifyError(f"Apify returned {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
        except ValueError as e:
            raise ApifyError(f"Apify returned non-JSON: {r.text[:300]}") from e

        if not isinstance(data, list):
            raise ApifyError(f"Expected list of items, got {type(data).__name__}: {str(data)[:300]}")
        return data
