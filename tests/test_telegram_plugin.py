"""Tests for content_parser.plugins.telegram.plugin — input validation + dispatch."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from content_parser.core.errors import AuthError, PluginError
from content_parser.core.redact import redact_spec as _redact_spec
from content_parser.plugins.telegram.plugin import (
    TelegramPlugin,
    _is_tg_host,
)


class IsTgHostTest(unittest.TestCase):
    def test_main_domains(self):
        self.assertTrue(_is_tg_host("t.me"))
        self.assertTrue(_is_tg_host("telegram.me"))
        self.assertTrue(_is_tg_host("T.ME"))

    def test_lookalike_rejected(self):
        self.assertFalse(_is_tg_host("evilt.me"))
        self.assertFalse(_is_tg_host("t.me.evil.example"))

    def test_empty(self):
        self.assertFalse(_is_tg_host(""))


class NormalizeChannelTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()

    def test_plain_username(self):
        self.assertEqual(self.p._normalize_channel("durov"), "durov")

    def test_at_handle(self):
        self.assertEqual(self.p._normalize_channel("@telegram"), "telegram")

    def test_url(self):
        self.assertEqual(
            self.p._normalize_channel("https://t.me/durov"),
            "durov",
        )

    def test_telegram_me_alias(self):
        self.assertEqual(
            self.p._normalize_channel("https://telegram.me/durov"),
            "durov",
        )

    def test_rejects_non_tg_host(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("https://evil.example/durov")

    def test_rejects_reserved_path(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("https://t.me/joinchat")
        with self.assertRaises(PluginError):
            self.p._normalize_channel("https://t.me/proxy")

    def test_rejects_too_short(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("dur")  # Telegram usernames are 5+ chars

    def test_rejects_starting_with_digit(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("123channel")

    def test_rejects_invalid_chars(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("name with spaces")

    def test_rejects_empty(self):
        with self.assertRaises(PluginError):
            self.p._normalize_channel("   ")


class ExtractPostUrlTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()

    def test_canonical_post_url(self):
        self.assertEqual(
            self.p._extract_post_url("https://t.me/durov/123"),
            "https://t.me/durov/123",
        )

    def test_telegram_me_normalized(self):
        self.assertEqual(
            self.p._extract_post_url("https://telegram.me/durov/123"),
            "https://t.me/durov/123",
        )

    def test_with_query_string(self):
        # The 's' subdomain or query params should be tolerated;
        # we extract just channel + msg id.
        self.assertEqual(
            self.p._extract_post_url("https://t.me/durov/123?single"),
            "https://t.me/durov/123",
        )

    def test_non_tg_host_rejected(self):
        self.assertIsNone(
            self.p._extract_post_url("https://evil.example/durov/123")
        )

    def test_channel_listing_rejected(self):
        # No message id → not a post
        self.assertIsNone(self.p._extract_post_url("https://t.me/durov"))

    def test_private_c_path_rejected(self):
        # /c/<chat_id>/<msg_id> is private channel — Apify scrapers usually can't read it
        self.assertIsNone(self.p._extract_post_url("https://t.me/c/123/456"))

    def test_reserved_path_rejected(self):
        self.assertIsNone(self.p._extract_post_url("https://t.me/joinchat/abc"))


class RedactSpecTest(unittest.TestCase):
    def test_strips_query(self):
        out = _redact_spec("post:https://t.me/durov/123?token=secret")
        self.assertNotIn("secret", out)

    def test_strips_fragment(self):
        out = _redact_spec("post:https://t.me/durov/123#access_token=xxx")
        self.assertNotIn("access_token", out)

    def test_truncates(self):
        self.assertLessEqual(len(_redact_spec("channel:" + "x" * 200)), 80)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()
        self.secrets = {"APIFY_API_TOKEN": "x"}

    def test_specs_carry_kind_prefix(self):
        specs = self.p.resolve(
            {
                "channel": ["durov", "@telegram"],
                "post_url": ["https://t.me/durov/123"],
            },
            {},
            self.secrets,
        )
        kinds = [s.split(":", 1)[0] for s in specs]
        self.assertEqual(kinds.count("channel"), 2)
        self.assertEqual(kinds.count("post"), 1)

    def test_dedupe(self):
        specs = self.p.resolve(
            {"channel": ["durov", "@durov", "https://t.me/durov"]},
            {},
            self.secrets,
        )
        self.assertEqual(len(specs), 1)

    def test_rejects_listing_url_in_post_field(self):
        with self.assertRaises(PluginError):
            self.p.resolve(
                {"post_url": ["https://t.me/durov"]},
                {},
                self.secrets,
            )


class FetchAuthGuardTest(unittest.TestCase):
    def test_missing_token_raises(self):
        p = TelegramPlugin()
        with self.assertRaises(AuthError):
            list(p.fetch(["channel:durov"], {}, {}))


class ActorIdValidationTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()

    def _patch_client(self):
        return patch("content_parser.plugins.telegram.plugin.ApifyClient")

    def test_default_used_when_empty(self):
        with self._patch_client() as MC:
            MC.return_value.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov"], {"actor_id": ""},
                {"APIFY_API_TOKEN": "x"},
            ))
            actor_id, _ = MC.return_value.run_actor.call_args[0]
            self.assertEqual(actor_id, "apify/telegram-channel-scraper")

    def test_default_used_when_whitespace(self):
        with self._patch_client() as MC:
            MC.return_value.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov"], {"actor_id": "   "},
                {"APIFY_API_TOKEN": "x"},
            ))
            actor_id, _ = MC.return_value.run_actor.call_args[0]
            self.assertEqual(actor_id, "apify/telegram-channel-scraper")

    def test_valid_username_actor_form(self):
        with self._patch_client() as MC:
            MC.return_value.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov"], {"actor_id": "73code/telegram-scraper"},
                {"APIFY_API_TOKEN": "x"},
            ))
            actor_id, _ = MC.return_value.run_actor.call_args[0]
            self.assertEqual(actor_id, "73code/telegram-scraper")

    def test_valid_tilde_form(self):
        with self._patch_client() as MC:
            MC.return_value.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov"], {"actor_id": "user~actor"},
                {"APIFY_API_TOKEN": "x"},
            ))
            self.assertEqual(MC.return_value.run_actor.call_args[0][0], "user~actor")

    def test_garbage_actor_id_raises(self):
        with self._patch_client() as MC:
            MC.return_value.run_actor.return_value = []
            for bad in ("noslash", "/missing", "missing/", "has spaces/x", "../../etc"):
                with self.subTest(bad=bad):
                    with self.assertRaises(PluginError):
                        list(self.p.fetch(
                            ["channel:durov"], {"actor_id": bad},
                            {"APIFY_API_TOKEN": "x"},
                        ))


class ApifyErrorMappingTest(unittest.TestCase):
    """ApifyError from the underlying client is wrapped in PluginError."""

    def setUp(self):
        self.p = TelegramPlugin()

    def test_channels_call_failure(self):
        from content_parser.clients.apify import ApifyError
        with patch("content_parser.plugins.telegram.plugin.ApifyClient") as MC:
            MC.return_value.run_actor.side_effect = ApifyError("simulated failure")
            with self.assertRaises(PluginError) as cm:
                list(self.p.fetch(
                    ["channel:durov"], {}, {"APIFY_API_TOKEN": "x"},
                ))
            self.assertIn("channels", str(cm.exception))
            self.assertIn("simulated failure", str(cm.exception))

    def test_posts_call_failure(self):
        from content_parser.clients.apify import ApifyError
        with patch("content_parser.plugins.telegram.plugin.ApifyClient") as MC:
            MC.return_value.run_actor.side_effect = ApifyError("posts went bad")
            with self.assertRaises(PluginError) as cm:
                list(self.p.fetch(
                    ["post:https://t.me/durov/1"], {},
                    {"APIFY_API_TOKEN": "x"},
                ))
            self.assertIn("posts", str(cm.exception))


class PrivateChannelRejectTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()

    def test_resolve_explicit_error_for_private_url(self):
        with self.assertRaises(PluginError) as cm:
            self.p.resolve(
                {"post_url": ["https://t.me/c/123/456"]},
                {},
                {"APIFY_API_TOKEN": "x"},
            )
        msg = str(cm.exception)
        self.assertIn("private", msg.lower())
        # Must mention /c/ pattern so user understands what's wrong
        self.assertTrue("/c/" in msg or "private-channel" in msg.lower())

    def test_is_private_channel_url_helper(self):
        self.assertTrue(self.p._is_private_channel_url("https://t.me/c/123/456"))
        self.assertFalse(self.p._is_private_channel_url("https://t.me/durov/123"))
        self.assertFalse(self.p._is_private_channel_url("https://evil.example/c/1/2"))


class FetchDispatchTest(unittest.TestCase):
    def setUp(self):
        self.p = TelegramPlugin()

    def _patch_client(self):
        return patch("content_parser.plugins.telegram.plugin.ApifyClient")

    def test_channels_only_makes_one_actor_call(self):
        with self._patch_client() as MC:
            inst = MC.return_value
            inst.run_actor.return_value = [
                {"id": 1, "channelUsername": "durov", "text": "x", "url": "https://t.me/durov/1"},
            ]
            list(self.p.fetch(
                ["channel:durov", "channel:telegram"],
                {"max_messages_per_channel": 5},
                {"APIFY_API_TOKEN": "x"},
            ))
            self.assertEqual(inst.run_actor.call_count, 1)
            actor_id, actor_input = inst.run_actor.call_args[0]
            self.assertIn("https://t.me/durov", actor_input["urls"])
            self.assertIn("https://t.me/telegram", actor_input["urls"])

    def test_post_urls_make_separate_actor_call(self):
        with self._patch_client() as MC:
            inst = MC.return_value
            inst.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov", "post:https://t.me/durov/123"],
                {"max_messages_per_channel": 5},
                {"APIFY_API_TOKEN": "x"},
            ))
            self.assertEqual(inst.run_actor.call_count, 2)

    def test_actor_id_overridable(self):
        with self._patch_client() as MC:
            inst = MC.return_value
            inst.run_actor.return_value = []
            list(self.p.fetch(
                ["channel:durov"],
                {"actor_id": "73code/telegram-scraper"},
                {"APIFY_API_TOKEN": "x"},
            ))
            actor_id, _ = inst.run_actor.call_args[0]
            self.assertEqual(actor_id, "73code/telegram-scraper")

    def test_dedupes_messages_by_item_id(self):
        same_msg = {
            "id": 1, "channelUsername": "durov", "text": "x",
            "url": "https://t.me/durov/1",
        }
        with self._patch_client() as MC:
            inst = MC.return_value
            # Same message returned in both channel and post calls
            inst.run_actor.side_effect = [[same_msg], [same_msg]]
            items = list(self.p.fetch(
                ["channel:durov", "post:https://t.me/durov/1"],
                {"max_messages_per_channel": 5},
                {"APIFY_API_TOKEN": "x"},
            ))
            self.assertEqual(len(items), 1)

    def test_caps_comment_count(self):
        msg = {
            "id": 1,
            "channelUsername": "durov",
            "text": "x",
            "url": "https://t.me/durov/1",
            "replies_data": [
                {"id": i, "from": {"username": f"u{i}"}, "text": f"c{i}"}
                for i in range(50)
            ],
        }
        with self._patch_client() as MC:
            inst = MC.return_value
            inst.run_actor.return_value = [msg]
            items = list(self.p.fetch(
                ["channel:durov"],
                {"max_messages_per_channel": 5, "max_comments_per_post": 10},
                {"APIFY_API_TOKEN": "x"},
            ))
            self.assertEqual(len(items), 1)
            self.assertEqual(len(items[0].comments), 10)


if __name__ == "__main__":
    unittest.main()
