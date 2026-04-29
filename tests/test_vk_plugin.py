"""Tests for content_parser.plugins.vk.plugin — input validation + dispatch."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import AuthError, PluginError, RateLimitError
from content_parser.plugins.vk.plugin import VKPlugin, _is_vk_host, _redact_spec


class NormalizeCommunityTest(unittest.TestCase):
    def setUp(self):
        self.p = VKPlugin()

    def test_screen_name(self):
        self.assertEqual(self.p._normalize_community("durov_says"), "durov_says")

    def test_url_with_screen_name(self):
        self.assertEqual(
            self.p._normalize_community("https://vk.com/durov_says"),
            "durov_says",
        )

    def test_club_prefix_url(self):
        self.assertEqual(self.p._normalize_community("https://vk.com/club12345"), "club12345")

    def test_public_prefix(self):
        self.assertEqual(self.p._normalize_community("public99"), "club99")

    def test_numeric_id(self):
        self.assertEqual(self.p._normalize_community("12345"), "12345")

    def test_strips_www_subdomain(self):
        # m.vk.com is recognized, but www.vk.com (a subdomain) — must also pass
        self.assertEqual(
            self.p._normalize_community("https://m.vk.com/awesome"),
            "awesome",
        )

    def test_rejects_non_vk_host(self):
        with self.assertRaises(PluginError):
            self.p._normalize_community("https://evil.example/durov_says")

    def test_rejects_reserved_path(self):
        # vk.com/feed → /feed/ is the news feed, not a community
        with self.assertRaises(PluginError):
            self.p._normalize_community("https://vk.com/feed")
        with self.assertRaises(PluginError):
            self.p._normalize_community("im")

    def test_rejects_empty(self):
        with self.assertRaises(PluginError):
            self.p._normalize_community("   ")

    def test_rejects_invalid_chars(self):
        with self.assertRaises(PluginError):
            self.p._normalize_community("name with spaces!")


class ExtractWallIdTest(unittest.TestCase):
    def setUp(self):
        self.p = VKPlugin()

    def test_canonical_post_url(self):
        self.assertEqual(
            self.p._extract_wall_id("https://vk.com/wall-12345_678"),
            "-12345_678",
        )

    def test_user_post_url(self):
        # positive owner_id = user wall
        self.assertEqual(
            self.p._extract_wall_id("https://vk.com/wall1_42"),
            "1_42",
        )

    def test_mobile_subdomain(self):
        self.assertEqual(
            self.p._extract_wall_id("https://m.vk.com/wall-1_2"),
            "-1_2",
        )

    def test_non_vk_host_rejected(self):
        self.assertIsNone(self.p._extract_wall_id("https://evil.example/wall-1_2"))

    def test_non_wall_path_rejected(self):
        self.assertIsNone(self.p._extract_wall_id("https://vk.com/durov_says"))


class IsVkHostTest(unittest.TestCase):
    def test_main_domain(self):
        self.assertTrue(_is_vk_host("vk.com"))
        self.assertTrue(_is_vk_host("m.vk.com"))
        self.assertTrue(_is_vk_host("VK.COM"))

    def test_lookalike_rejected(self):
        self.assertFalse(_is_vk_host("evilvk.com"))
        self.assertFalse(_is_vk_host("vk.com.evil.example"))

    def test_empty(self):
        self.assertFalse(_is_vk_host(""))


class RedactSpecTest(unittest.TestCase):
    def test_strips_query(self):
        out = _redact_spec("post:https://vk.com/wall-1_2?token=secret")
        self.assertNotIn("secret", out)
        self.assertIn("?…", out)

    def test_strips_fragment(self):
        out = _redact_spec("post:https://vk.com/wall-1_2#access_token=xxx")
        self.assertNotIn("access_token", out)

    def test_truncates_long(self):
        out = _redact_spec("community:" + "x" * 200)
        self.assertLessEqual(len(out), 80)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = VKPlugin()
        self.secrets = {"VK_ACCESS_TOKEN": "x"}

    def test_specs_carry_kind_prefix(self):
        specs = self.p.resolve(
            {
                "query": ["маркетинг"],
                "community": ["durov_says"],
                "post_url": ["https://vk.com/wall-1_2"],
            },
            {},
            self.secrets,
        )
        kinds = [s.split(":", 1)[0] for s in specs]
        self.assertEqual(kinds.count("query"), 1)
        self.assertEqual(kinds.count("community"), 1)
        self.assertEqual(kinds.count("post"), 1)

    def test_dedupe(self):
        specs = self.p.resolve(
            {"community": ["durov_says", "https://vk.com/durov_says", "durov_says"]},
            {},
            self.secrets,
        )
        self.assertEqual(len(specs), 1)

    def test_rejects_non_wall_url_in_post_field(self):
        with self.assertRaises(PluginError):
            self.p.resolve(
                {"post_url": ["https://vk.com/durov_says"]},
                {},
                self.secrets,
            )


class FetchAuthGuardTest(unittest.TestCase):
    def test_missing_token_raises_auth(self):
        p = VKPlugin()
        with self.assertRaises(AuthError):
            list(p.fetch(["community:durov_says"], {}, {}))


class ClientErrorMappingTest(unittest.TestCase):
    """VKClient maps known error_code → AuthError / RateLimitError / PluginError."""

    def _mock_response(self, payload, status=200, ok=True):
        m = MagicMock()
        m.ok = ok
        m.status_code = status
        m.json.return_value = payload
        return m

    def test_auth_error(self):
        from content_parser.plugins.vk.client import VKClient

        with patch("content_parser.plugins.vk.client.requests.post") as rp:
            rp.return_value = self._mock_response({
                "error": {"error_code": 5, "error_msg": "User authorization failed"}
            })
            client = VKClient("bad_token")
            with self.assertRaises(AuthError) as cm:
                client.call("groups.search", q="x")
            self.assertIn("authorization", str(cm.exception).lower())

    def test_rate_limit(self):
        from content_parser.plugins.vk.client import VKClient

        with patch("content_parser.plugins.vk.client.requests.post") as rp:
            rp.return_value = self._mock_response({
                "error": {"error_code": 6, "error_msg": "Too many requests per second"}
            })
            with self.assertRaises(RateLimitError):
                VKClient("x").call("groups.search", q="x")

    def test_other_error(self):
        from content_parser.plugins.vk.client import VKClient

        with patch("content_parser.plugins.vk.client.requests.post") as rp:
            rp.return_value = self._mock_response({
                "error": {"error_code": 100, "error_msg": "Param missing"}
            })
            with self.assertRaises(PluginError):
                VKClient("x").call("groups.search", q="x")

    def test_token_in_body_not_query(self):
        from content_parser.plugins.vk.client import VKClient

        with patch("content_parser.plugins.vk.client.requests.post") as rp:
            rp.return_value = self._mock_response({"response": {"items": []}})
            VKClient("MY_SECRET_TOKEN").call("groups.search", q="x", count=10)

            args, kwargs = rp.call_args
            # token must not appear in URL
            self.assertNotIn("MY_SECRET_TOKEN", args[0])
            self.assertNotIn("MY_SECRET_TOKEN", str(kwargs.get("params") or {}))
            # token must appear in form-encoded body
            self.assertEqual(kwargs["data"]["access_token"], "MY_SECRET_TOKEN")
            self.assertEqual(kwargs["data"]["q"], "x")


class FetchDispatchTest(unittest.TestCase):
    """Verify fetch routes each spec kind to the right VK API method."""

    def setUp(self):
        self.p = VKPlugin()
        self.secrets = {"VK_ACCESS_TOKEN": "tok"}

    def _make_client(self):
        client = MagicMock()
        return client

    def _patch_client(self, client):
        return patch(
            "content_parser.plugins.vk.plugin.VKClient",
            return_value=client,
        )

    def test_query_calls_groups_search_and_walls(self):
        client = self._make_client()

        def fake_call(method, **params):
            if method == "groups.search":
                return {"items": [{"id": 1, "name": "G1"}, {"id": 2, "name": "G2"}]}
            if method == "wall.get":
                return {"items": [{"id": 100, "owner_id": params["owner_id"], "date": 1, "text": "x"}],
                        "profiles": [], "groups": []}
            return None

        client.call.side_effect = fake_call
        with self._patch_client(client):
            items = list(self.p.fetch(
                ["query:маркетинг"],
                {"max_communities_per_query": 2, "max_posts_per_input": 5,
                 "fetch_comments": False},
                self.secrets,
            ))
        self.assertEqual(len(items), 2)
        owners = sorted(it.author_id for it in items)
        self.assertEqual(owners, ["-1", "-2"])

    def test_community_resolves_then_fetches_wall(self):
        client = self._make_client()

        def fake_call(method, **params):
            if method == "groups.getById":
                return {"groups": [{"id": 12345, "name": "Awesome", "screen_name": "awesome"}]}
            if method == "wall.get":
                return {"items": [{"id": 7, "owner_id": -12345, "date": 1, "text": "hi"}],
                        "profiles": [], "groups": []}
            return None

        client.call.side_effect = fake_call
        with self._patch_client(client):
            items = list(self.p.fetch(
                ["community:awesome"],
                {"max_posts_per_input": 5, "fetch_comments": False},
                self.secrets,
            ))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, "-12345_7")
        self.assertEqual(items[0].author, "Awesome")

    def test_post_calls_wall_getById(self):
        client = self._make_client()

        def fake_call(method, **params):
            if method == "wall.getById":
                self.assertEqual(params["posts"], "-12345_7")
                return {
                    "items": [{"id": 7, "owner_id": -12345, "date": 1, "text": "p"}],
                    "profiles": [],
                    "groups": [{"id": 12345, "name": "Awesome"}],
                }
            return None

        client.call.side_effect = fake_call
        with self._patch_client(client):
            items = list(self.p.fetch(
                ["post:-12345_7"], {"fetch_comments": False}, self.secrets,
            ))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].author, "Awesome")

    def test_dedupes_same_post_from_multiple_inputs(self):
        client = self._make_client()
        same_post = {"id": 7, "owner_id": -12345, "date": 1, "text": "same"}

        def fake_call(method, **params):
            if method == "wall.get":
                return {"items": [same_post], "profiles": [], "groups": []}
            if method == "groups.getById":
                return {"groups": [{"id": 12345, "name": "Awesome"}]}
            if method == "wall.getById":
                return {"items": [same_post], "profiles": [],
                        "groups": [{"id": 12345, "name": "Awesome"}]}
            return None

        client.call.side_effect = fake_call
        with self._patch_client(client):
            items = list(self.p.fetch(
                ["community:awesome", "post:-12345_7"],
                {"max_posts_per_input": 5, "fetch_comments": False},
                self.secrets,
            ))
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
