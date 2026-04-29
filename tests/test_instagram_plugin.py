"""Tests for content_parser.plugins.instagram.plugin — input validation + dispatch."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import PluginError
from content_parser.plugins.instagram.plugin import InstagramPlugin


class NormalizeAccountTest(unittest.TestCase):
    def setUp(self):
        self.p = InstagramPlugin()

    def test_plain_username(self):
        self.assertEqual(self.p._normalize_account("nasa"), "nasa")

    def test_at_handle(self):
        self.assertEqual(self.p._normalize_account("@durov"), "durov")

    def test_url_with_query(self):
        self.assertEqual(
            self.p._normalize_account("https://instagram.com/zuck/?hl=en"),
            "zuck",
        )

    def test_dotted_underscored(self):
        self.assertEqual(self.p._normalize_account("user.name_42"), "user.name_42")

    def test_rejects_post_url(self):
        with self.assertRaises(PluginError):
            self.p._normalize_account("https://www.instagram.com/p/abc/")

    def test_rejects_reel_url(self):
        with self.assertRaises(PluginError):
            self.p._normalize_account("https://www.instagram.com/reel/abc/")

    def test_rejects_invalid_chars(self):
        with self.assertRaises(PluginError):
            self.p._normalize_account("not a valid name!")


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = InstagramPlugin()

    def test_specs_carry_kind_prefix(self):
        specs = self.p.resolve(
            {
                "hashtag": ["smm", "#marketing"],
                "account": ["nasa"],
                "post_url": ["https://www.instagram.com/reel/xyz/"],
            },
            {},
            {"APIFY_API_TOKEN": "x"},
        )
        kinds = [s.split(":", 1)[0] for s in specs]
        self.assertEqual(kinds.count("hashtag"), 2)
        self.assertEqual(kinds.count("account"), 1)
        self.assertEqual(kinds.count("post"), 1)

    def test_dedupes(self):
        specs = self.p.resolve(
            {"hashtag": ["smm", "#smm", "smm"]}, {}, {"APIFY_API_TOKEN": "x"}
        )
        self.assertEqual(len(specs), 1)

    def test_rejects_account_url_in_post_url_field(self):
        with self.assertRaises(PluginError):
            self.p.resolve(
                {"post_url": ["https://www.instagram.com/nasa/"]},
                {},
                {"APIFY_API_TOKEN": "x"},
            )


class FetchDispatchTest(unittest.TestCase):
    """Verifies fetch() splits by input kind into separate Apify calls."""

    def setUp(self):
        self.p = InstagramPlugin()
        self.fake_post = {"id": "1", "shortCode": "AAA", "url": "https://insta/p/AAA"}

    def _run(self, specs, settings=None):
        settings = settings or {"max_posts_per_input": 5, "results_type": "posts"}
        with patch("content_parser.plugins.instagram.plugin.ApifyClient") as MC:
            MC.return_value.run_actor.return_value = [self.fake_post]
            list(self.p.fetch(specs, settings, {"APIFY_API_TOKEN": "x"}))
            return MC.return_value.run_actor.call_args_list

    def test_listings_only_makes_one_call(self):
        calls = self._run(["account:https://insta/nasa/", "hashtag:https://insta/explore/tags/x/"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1]["resultsType"], "posts")

    def test_post_urls_only_makes_one_details_call(self):
        calls = self._run(["post:https://insta/p/AAA/"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1]["resultsType"], "details")

    def test_mixed_makes_two_calls(self):
        calls = self._run([
            "account:https://insta/nasa/",
            "post:https://insta/p/AAA/",
        ])
        self.assertEqual(len(calls), 2)
        types = sorted(c[0][1]["resultsType"] for c in calls)
        self.assertEqual(types, ["details", "posts"])

    def test_results_type_setting_applied_to_listings(self):
        calls = self._run(
            ["account:https://insta/nasa/"],
            settings={"max_posts_per_input": 5, "results_type": "comments"},
        )
        self.assertEqual(calls[0][0][1]["resultsType"], "comments")


class ApifyClientAuthTest(unittest.TestCase):
    """ApifyClient sends the token in Authorization header, not query string."""

    def test_uses_bearer_header(self):
        from content_parser.plugins.instagram.apify_client import ApifyClient

        with patch(
            "content_parser.plugins.instagram.apify_client.requests.post"
        ) as rp:
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            resp.json.return_value = []
            rp.return_value = resp

            ApifyClient("MY_TOKEN").run_actor("apify/x", {})

            kwargs = rp.call_args.kwargs
            self.assertEqual(kwargs["headers"], {"Authorization": "Bearer MY_TOKEN"})
            self.assertNotIn("token", kwargs.get("params", {}))


if __name__ == "__main__":
    unittest.main()
