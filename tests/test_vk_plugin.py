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


class RetryOnRateLimitTest(unittest.TestCase):
    """VKClient retries with exponential backoff on RateLimitError before giving up."""

    def _mock_response(self, payload, ok=True, status=200):
        m = MagicMock()
        m.ok = ok
        m.status_code = status
        m.json.return_value = payload
        return m

    def test_retries_then_succeeds(self):
        from content_parser.plugins.vk.client import VKClient

        sleeps: list[float] = []
        rate_limit_payload = {"error": {"error_code": 6, "error_msg": "Too many requests per second"}}
        success_payload = {"response": {"items": []}}

        with patch("content_parser.plugins.vk.client.requests.Session") as MockSession, \
             patch.object(VKClient, "_sleep", staticmethod(lambda s: sleeps.append(s))):
            session = MagicMock()
            session.post.side_effect = [
                self._mock_response(rate_limit_payload),
                self._mock_response(rate_limit_payload),
                self._mock_response(success_payload),
            ]
            MockSession.return_value = session

            client = VKClient("x")
            resp = client.call("groups.search", q="x")

            self.assertEqual(session.post.call_count, 3)
            self.assertEqual(resp, {"items": []})
            self.assertEqual(sleeps, [1.0, 2.0])  # exponential backoff

    def test_gives_up_after_max_retries(self):
        from content_parser.plugins.vk.client import VKClient

        rate_limit_payload = {"error": {"error_code": 6, "error_msg": "x"}}

        with patch("content_parser.plugins.vk.client.requests.Session") as MockSession, \
             patch.object(VKClient, "_sleep", staticmethod(lambda s: None)):
            session = MagicMock()
            session.post.return_value = self._mock_response(rate_limit_payload)
            MockSession.return_value = session

            client = VKClient("x", max_rate_limit_retries=2)
            with self.assertRaises(RateLimitError):
                client.call("groups.search", q="x")

            # Initial call + 2 retries = 3 total.
            self.assertEqual(session.post.call_count, 3)

    def test_auth_error_not_retried(self):
        from content_parser.plugins.vk.client import VKClient

        auth_payload = {"error": {"error_code": 5, "error_msg": "Auth failed"}}

        with patch("content_parser.plugins.vk.client.requests.Session") as MockSession, \
             patch.object(VKClient, "_sleep", staticmethod(lambda s: None)):
            session = MagicMock()
            session.post.return_value = self._mock_response(auth_payload)
            MockSession.return_value = session

            client = VKClient("x")
            with self.assertRaises(AuthError):
                client.call("groups.search", q="x")

            # Only one call — no retries on AuthError.
            self.assertEqual(session.post.call_count, 1)


class CommentPaginationTest(unittest.TestCase):
    """_fetch_comments correctness: cap respected, short-circuit on count."""

    def setUp(self):
        self.p = VKPlugin()

    def _make_client(self, responses: list[dict]) -> MagicMock:
        client = MagicMock()
        client.call.side_effect = responses
        return client

    @staticmethod
    def _comment(cid, replies=None):
        c = {
            "id": cid,
            "from_id": 100 + cid,
            "date": 1_700_000_000,
            "text": f"comment {cid}",
            "likes": {"count": 0},
        }
        if replies:
            c["thread"] = {"items": replies}
        return c

    def test_top_level_cap_enforced_exactly(self):
        # 200 top-level comments available, max=50. Should yield exactly 50.
        all_comments = [self._comment(i) for i in range(200)]
        responses = [
            {"items": all_comments[:100], "profiles": [], "groups": [], "count": 200},
            {"items": all_comments[100:200], "profiles": [], "groups": [], "count": 200},
        ]
        client = self._make_client(responses)
        out = self.p._fetch_comments(
            client, owner_id=-1, post_id=1, max_comments=50, depth="top_level"
        )
        self.assertEqual(len(out), 50)
        # We only need one page (50 ≤ 100 page size)
        self.assertEqual(client.call.call_count, 1)

    def test_depth_all_does_not_overshoot_cap(self):
        # 1 top-level with 200 replies, max=10 → exactly 10, not 11.
        replies = [self._comment(1000 + i) for i in range(200)]
        top = [self._comment(1, replies=replies)]
        responses = [
            {"items": top, "profiles": [], "groups": [], "count": 1},
        ]
        client = self._make_client(responses)
        out = self.p._fetch_comments(
            client, owner_id=-1, post_id=1, max_comments=10, depth="all"
        )
        self.assertEqual(len(out), 10)
        # First should be the top-level; rest replies with parent_id=1
        self.assertIsNone(out[0].parent_id)
        for c in out[1:]:
            self.assertEqual(c.parent_id, "1")

    def test_pagination_short_circuit_via_count(self):
        # Page 1: 100 items, count=100 → no second page.
        all_comments = [self._comment(i) for i in range(100)]
        responses = [
            {"items": all_comments, "profiles": [], "groups": [], "count": 100},
        ]
        client = self._make_client(responses)
        out = self.p._fetch_comments(
            client, owner_id=-1, post_id=1, max_comments=500, depth="top_level"
        )
        self.assertEqual(len(out), 100)
        # Critically: only ONE call, no useless extra request.
        self.assertEqual(client.call.call_count, 1)

    def test_pagination_continues_when_count_higher_than_page(self):
        page1 = [self._comment(i) for i in range(100)]
        page2 = [self._comment(100 + i) for i in range(50)]
        responses = [
            {"items": page1, "profiles": [], "groups": [], "count": 150},
            {"items": page2, "profiles": [], "groups": [], "count": 150},
        ]
        client = self._make_client(responses)
        out = self.p._fetch_comments(
            client, owner_id=-1, post_id=1, max_comments=500, depth="top_level"
        )
        self.assertEqual(len(out), 150)
        self.assertEqual(client.call.call_count, 2)


class AdapterDefensiveTest(unittest.TestCase):
    def test_post_to_item_raises_on_missing_owner_id(self):
        from content_parser.plugins.vk.adapter import post_to_item
        with self.assertRaises(ValueError):
            post_to_item({"id": 1})

    def test_post_to_item_raises_on_missing_id(self):
        from content_parser.plugins.vk.adapter import post_to_item
        with self.assertRaises(ValueError):
            post_to_item({"owner_id": -1})


class ClientErrorMappingTest(unittest.TestCase):
    """VKClient maps known error_code → AuthError / RateLimitError / PluginError."""

    def _mock_response(self, payload, status=200, ok=True):
        m = MagicMock()
        m.ok = ok
        m.status_code = status
        m.json.return_value = payload
        return m

    def _patched_session(self, response):
        """Build a context manager that patches requests.Session in the client."""
        session = MagicMock()
        session.post.return_value = response
        return patch(
            "content_parser.plugins.vk.client.requests.Session",
            return_value=session,
        ), session

    def test_auth_error(self):
        from content_parser.plugins.vk.client import VKClient

        ctx, session = self._patched_session(self._mock_response({
            "error": {"error_code": 5, "error_msg": "User authorization failed"}
        }))
        with ctx, patch.object(VKClient, "_sleep", staticmethod(lambda s: None)):
            client = VKClient("bad_token")
            with self.assertRaises(AuthError) as cm:
                client.call("groups.search", q="x")
            self.assertIn("authorization", str(cm.exception).lower())

    def test_rate_limit(self):
        from content_parser.plugins.vk.client import VKClient

        ctx, session = self._patched_session(self._mock_response({
            "error": {"error_code": 6, "error_msg": "Too many requests per second"}
        }))
        with ctx, patch.object(VKClient, "_sleep", staticmethod(lambda s: None)):
            with self.assertRaises(RateLimitError):
                VKClient("x", max_rate_limit_retries=0).call("groups.search", q="x")

    def test_other_error(self):
        from content_parser.plugins.vk.client import VKClient

        ctx, session = self._patched_session(self._mock_response({
            "error": {"error_code": 100, "error_msg": "Param missing"}
        }))
        with ctx, patch.object(VKClient, "_sleep", staticmethod(lambda s: None)):
            with self.assertRaises(PluginError):
                VKClient("x").call("groups.search", q="x")

    def test_token_in_body_not_query(self):
        from content_parser.plugins.vk.client import VKClient

        ctx, session = self._patched_session(self._mock_response({"response": {"items": []}}))
        with ctx:
            VKClient("MY_SECRET_TOKEN").call("groups.search", q="x", count=10)

            args, kwargs = session.post.call_args
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
