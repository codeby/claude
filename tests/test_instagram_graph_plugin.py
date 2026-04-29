"""Tests for content_parser.plugins.instagram_graph.plugin."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import AuthError, PluginError, RateLimitError
from content_parser.plugins.instagram_graph.plugin import InstagramGraphPlugin


VALID_ACCOUNT_ID = "17841405822304914"
VALID_POST_ID = "17895695668004550"


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = InstagramGraphPlugin()

    def test_valid_account_id(self):
        specs = self.p.resolve({"account": [VALID_ACCOUNT_ID]}, {}, {})
        self.assertEqual(specs, [f"account:{VALID_ACCOUNT_ID}"])

    def test_valid_post_id(self):
        specs = self.p.resolve({"post_id": [VALID_POST_ID]}, {}, {})
        self.assertEqual(specs, [f"post:{VALID_POST_ID}"])

    def test_dedupes(self):
        specs = self.p.resolve(
            {"account": [VALID_ACCOUNT_ID, VALID_ACCOUNT_ID]}, {}, {},
        )
        self.assertEqual(len(specs), 1)

    def test_rejects_non_numeric_account(self):
        with self.assertRaises(PluginError):
            self.p.resolve({"account": ["not_an_id"]}, {}, {})

    def test_rejects_too_short_account(self):
        with self.assertRaises(PluginError):
            self.p.resolve({"account": ["123"]}, {}, {})

    def test_rejects_url_in_account_field(self):
        with self.assertRaises(PluginError):
            self.p.resolve(
                {"account": ["https://instagram.com/myaccount"]}, {}, {},
            )

    def test_rejects_invalid_post_id(self):
        with self.assertRaises(PluginError):
            self.p.resolve({"post_id": ["abc"]}, {}, {})


class FetchAuthTest(unittest.TestCase):
    def test_missing_token_raises(self):
        p = InstagramGraphPlugin()
        with self.assertRaises(AuthError):
            list(p.fetch([f"account:{VALID_ACCOUNT_ID}"], {}, {}))


class FetchDispatchTest(unittest.TestCase):
    def setUp(self):
        self.p = InstagramGraphPlugin()
        self.token = "TEST_TOKEN"
        self.secrets = {"INSTAGRAM_ACCESS_TOKEN": self.token}

    def _patched_client(self, calls: dict):
        """calls = {(method_name, path): return_value}."""
        client = MagicMock()
        client.get.side_effect = lambda path, params=None: calls[("get", path)]

        def paginated(path, params=None, max_items=None):
            return calls[("paginated", path)]

        client.get_paginated.side_effect = paginated
        return patch("content_parser.plugins.instagram_graph.plugin.GraphClient", return_value=client), client

    def test_account_path_calls_user_then_media(self):
        media1 = {
            "id": "1001", "media_type": "IMAGE",
            "caption": "post one", "permalink": "https://insta/p/1",
            "like_count": 100, "comments_count": 5,
            "timestamp": "2026-04-01T00:00:00+0000",
            "owner": {"id": VALID_ACCOUNT_ID, "username": "myaccount"},
        }
        media2 = {
            "id": "1002", "media_type": "REEL",
            "caption": "reel two", "permalink": "https://insta/r/2",
            "owner": {"id": VALID_ACCOUNT_ID, "username": "myaccount"},
        }
        calls = {
            ("get", VALID_ACCOUNT_ID): {"username": "myaccount"},
            ("paginated", f"{VALID_ACCOUNT_ID}/media"): [media1, media2],
            ("paginated", "1001/comments"): [],
            ("paginated", "1002/comments"): [],
            ("get", "1001/insights"): {"data": [{"name": "reach", "values": [{"value": 500}]}]},
            ("get", "1002/insights"): {"data": [{"name": "plays", "values": [{"value": 9000}]}]},
        }
        ctx, client = self._patched_client(calls)
        with ctx:
            items = list(self.p.fetch(
                [f"account:{VALID_ACCOUNT_ID}"],
                {"max_posts_per_account": 5, "fetch_insights": True},
                self.secrets,
            ))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].author, "myaccount")
        self.assertEqual(items[0].media["reach"], 500)
        self.assertEqual(items[1].media["plays"], 9000)

    def test_post_path_calls_media_directly(self):
        media = {
            "id": VALID_POST_ID, "media_type": "VIDEO",
            "caption": "single", "permalink": "https://insta/p/x",
            "owner": {"id": VALID_ACCOUNT_ID, "username": "myaccount"},
        }
        calls = {
            ("get", VALID_POST_ID): media,
            ("paginated", f"{VALID_POST_ID}/comments"): [],
            ("get", f"{VALID_POST_ID}/insights"): {"data": []},
        }
        ctx, client = self._patched_client(calls)
        with ctx:
            items = list(self.p.fetch(
                [f"post:{VALID_POST_ID}"],
                {"fetch_insights": True},
                self.secrets,
            ))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, VALID_POST_ID)

    def test_dedupes_same_media_from_account_and_post(self):
        media = {
            "id": "1001", "media_type": "IMAGE", "caption": "x",
            "permalink": "https://insta/p/1",
            "owner": {"id": VALID_ACCOUNT_ID, "username": "myaccount"},
        }
        calls = {
            ("get", VALID_ACCOUNT_ID): {"username": "myaccount"},
            ("paginated", f"{VALID_ACCOUNT_ID}/media"): [media],
            ("get", "1001"): media,
            ("paginated", "1001/comments"): [],
        }
        ctx, _ = self._patched_client(calls)
        with ctx:
            items = list(self.p.fetch(
                [f"account:{VALID_ACCOUNT_ID}", "post:1001"],
                {"max_posts_per_account": 5, "fetch_insights": False},
                self.secrets,
            ))
        self.assertEqual(len(items), 1)

    def test_fetch_insights_false_skips_insight_calls(self):
        media = {
            "id": VALID_POST_ID, "media_type": "IMAGE", "caption": "x",
            "permalink": "https://insta/p/x",
        }
        calls = {
            ("get", VALID_POST_ID): media,
            ("paginated", f"{VALID_POST_ID}/comments"): [],
        }
        ctx, client = self._patched_client(calls)
        with ctx:
            list(self.p.fetch(
                [f"post:{VALID_POST_ID}"],
                {"fetch_insights": False},
                self.secrets,
            ))
        # Verify NO call was made to /insights
        for call in client.get.call_args_list:
            args = call.args
            self.assertNotIn("insights", args[0])

    def test_insights_metric_selection_for_reel_vs_post(self):
        """Reels use plays/total_interactions; feed posts use impressions."""
        client = MagicMock()
        captured_metrics = []

        def get_side(path, params=None):
            if path.endswith("/insights"):
                captured_metrics.append(params.get("metric"))
                return {"data": []}
            if path == VALID_POST_ID:
                # Two consecutive calls for two different scenarios — the test
                # asks for posts in two separate fetch calls.
                return reel_media if "reel_call" in params else feed_media
            return {}

        # Use simple branches: dispatch based on which call we're making.
        reel_media = {
            "id": VALID_POST_ID, "media_type": "REEL", "caption": "r",
            "permalink": "https://insta/r",
        }
        feed_media = {
            "id": VALID_POST_ID, "media_type": "IMAGE", "caption": "f",
            "permalink": "https://insta/p",
        }

        # Reel scenario
        client.get.side_effect = lambda path, params=None: (
            reel_media if path == VALID_POST_ID else (
                ({"data": []}, captured_metrics.append(params.get("metric")))[0]
                if path.endswith("/insights") else {}
            )
        )
        client.get_paginated.return_value = []
        with patch("content_parser.plugins.instagram_graph.plugin.GraphClient", return_value=client):
            list(self.p.fetch(
                [f"post:{VALID_POST_ID}"],
                {"fetch_insights": True, "fetch_comments": False},
                self.secrets,
            ))
        self.assertEqual(len(captured_metrics), 1)
        # Reel metric set
        self.assertIn("plays", captured_metrics[0])
        self.assertIn("total_interactions", captured_metrics[0])

        # Feed-post scenario
        captured_metrics.clear()
        client.get.side_effect = lambda path, params=None: (
            feed_media if path == VALID_POST_ID else (
                ({"data": []}, captured_metrics.append(params.get("metric")))[0]
                if path.endswith("/insights") else {}
            )
        )
        with patch("content_parser.plugins.instagram_graph.plugin.GraphClient", return_value=client):
            list(self.p.fetch(
                [f"post:{VALID_POST_ID}"],
                {"fetch_insights": True, "fetch_comments": False},
                self.secrets,
            ))
        self.assertEqual(len(captured_metrics), 1)
        # Feed-post metric set — has impressions, no plays
        self.assertIn("impressions", captured_metrics[0])
        self.assertNotIn("plays", captured_metrics[0])

    def test_insights_failure_does_not_break_run(self):
        media = {
            "id": VALID_POST_ID, "media_type": "REEL", "caption": "x",
            "permalink": "https://insta/r/x",
        }
        client = MagicMock()

        def get_side(path, params=None):
            if path == VALID_POST_ID:
                return media
            if path.endswith("/insights"):
                raise PluginError("permissions")
            return {}
        client.get.side_effect = get_side
        client.get_paginated.return_value = []

        with patch("content_parser.plugins.instagram_graph.plugin.GraphClient", return_value=client):
            items = list(self.p.fetch(
                [f"post:{VALID_POST_ID}"],
                {"fetch_insights": True},
                self.secrets,
            ))
        self.assertEqual(len(items), 1)
        # plays/reach should be absent because insights failed
        self.assertNotIn("plays", items[0].media)


if __name__ == "__main__":
    unittest.main()
