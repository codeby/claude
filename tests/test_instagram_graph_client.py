"""Tests for content_parser.plugins.instagram_graph.client — Graph API HTTP client."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import AuthError, PluginError, RateLimitError
from content_parser.plugins.instagram_graph.client import GraphClient


def _resp(payload, *, status=200, ok=True):
    m = MagicMock()
    m.ok = ok
    m.status_code = status
    m.json.return_value = payload
    m.text = str(payload)
    return m


class GraphClientTokenTest(unittest.TestCase):
    """Token must reach the URL as `access_token=` query param every call."""

    def _patched_session(self, response):
        session = MagicMock()
        session.get.return_value = response
        return patch("content_parser.plugins.instagram_graph.client.requests.Session",
                     return_value=session), session

    def test_token_attached_to_query(self):
        ctx, session = self._patched_session(_resp({"id": "1", "username": "x"}))
        with ctx:
            GraphClient("MY_TOKEN").get("17841", params={"fields": "username"})
        kwargs = session.get.call_args.kwargs
        self.assertEqual(kwargs["params"]["access_token"], "MY_TOKEN")
        self.assertEqual(kwargs["params"]["fields"], "username")

    def test_user_supplied_token_overrides_embedded(self):
        # If the user passes an access_token in params, OUR client overrides it
        # — defense in case a `next` URL embeds a different token.
        ctx, session = self._patched_session(_resp({"id": "1"}))
        with ctx:
            GraphClient("REAL_TOKEN").get("path", params={"access_token": "OTHER", "fields": "id"})
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["access_token"], "REAL_TOKEN")


class GraphClientErrorMappingTest(unittest.TestCase):
    def _patch(self, response):
        session = MagicMock()
        session.get.return_value = response
        return patch("content_parser.plugins.instagram_graph.client.requests.Session",
                     return_value=session)

    def test_401_raises_auth(self):
        with self._patch(_resp({"error": {"code": 190, "message": "invalid token"}}, status=401, ok=False)), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(AuthError):
                GraphClient("x").get("me")

    def test_permission_code_10_raises_auth(self):
        with self._patch(_resp({"error": {"code": 10, "message": "no perm"}}, status=400, ok=False)), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(AuthError) as cm:
                GraphClient("x").get("me")
            self.assertIn("permissions", str(cm.exception).lower())

    def test_429_raises_rate_limit(self):
        with self._patch(_resp({"error": {"code": 4, "message": "rate"}}, status=429, ok=False)), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(RateLimitError):
                GraphClient("x", max_rate_limit_retries=0).get("me")

    def test_5xx_treated_as_retryable(self):
        with self._patch(_resp({}, status=503, ok=False)), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(RateLimitError):
                GraphClient("x", max_rate_limit_retries=0).get("me")

    def test_other_4xx_raises_plugin_error(self):
        with self._patch(_resp({"error": {"code": 100, "message": "bad param"}}, status=400, ok=False)), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(PluginError) as cm:
                GraphClient("x").get("me")
            self.assertIn("100", str(cm.exception))


class GraphClientRetryTest(unittest.TestCase):
    def test_retries_on_429_then_succeeds(self):
        sleeps: list[float] = []
        responses = [
            _resp({"error": {"code": 4, "message": "rate"}}, status=429, ok=False),
            _resp({"id": "ok"}),
        ]
        session = MagicMock()
        session.get.side_effect = responses
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session), \
             patch.object(GraphClient, "_sleep", staticmethod(lambda s: sleeps.append(s))):
            result = GraphClient("x").get("me")
        self.assertEqual(result, {"id": "ok"})
        self.assertEqual(sleeps, [2.0])

    def test_exhausts_retries_then_raises(self):
        responses = [_resp({"error": {"code": 4, "message": "rate"}}, status=429, ok=False)] * 5
        session = MagicMock()
        session.get.side_effect = responses
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session), \
             patch.object(GraphClient, "_sleep"):
            with self.assertRaises(RateLimitError):
                GraphClient("x", max_rate_limit_retries=2).get("me")
        # 1 initial + 2 retries = 3
        self.assertEqual(session.get.call_count, 3)


class GraphClientPaginationTest(unittest.TestCase):
    def test_walks_next_url_and_caps_at_max_items(self):
        page1 = {"data": [{"id": str(i)} for i in range(10)],
                 "paging": {"next": "https://graph.facebook.com/v19.0/x/media?fields=id&after=ABC"}}
        page2 = {"data": [{"id": str(i)} for i in range(10, 15)]}
        session = MagicMock()
        session.get.side_effect = [_resp(page1), _resp(page2)]
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session):
            client = GraphClient("x")
            items = client.get_paginated("17841/media", params={"fields": "id"})
        self.assertEqual(len(items), 15)

    def test_max_items_stops_early(self):
        page1 = {"data": [{"id": str(i)} for i in range(10)],
                 "paging": {"next": "https://graph.facebook.com/v19.0/x/media?after=A"}}
        session = MagicMock()
        session.get.return_value = _resp(page1)
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session):
            items = GraphClient("x").get_paginated("17841/media", max_items=5)
        self.assertEqual(len(items), 5)
        # Only ONE call — we hit max_items inside the first page
        self.assertEqual(session.get.call_count, 1)

    def test_no_next_url_stops(self):
        session = MagicMock()
        session.get.return_value = _resp({"data": [{"id": "1"}]})
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session):
            items = GraphClient("x").get_paginated("path")
        self.assertEqual(items, [{"id": "1"}])

    def test_strips_embedded_token_in_next_url(self):
        page1 = {
            "data": [{"id": "1"}],
            "paging": {"next": "https://graph.facebook.com/v19.0/x/media?after=A&access_token=EMBEDDED"},
        }
        page2 = {"data": [{"id": "2"}]}
        session = MagicMock()
        session.get.side_effect = [_resp(page1), _resp(page2)]
        with patch("content_parser.plugins.instagram_graph.client.requests.Session",
                   return_value=session):
            GraphClient("OUR_TOKEN").get_paginated("x/media")
        # Second call should use OUR token, not EMBEDDED
        second_call_params = session.get.call_args_list[1].kwargs["params"]
        self.assertEqual(second_call_params["access_token"], "OUR_TOKEN")


if __name__ == "__main__":
    unittest.main()
