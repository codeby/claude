"""Tests for content_parser.plugins.telegram.adapter — defensive field mapping."""
from __future__ import annotations

import unittest

from content_parser.plugins.telegram.adapter import (
    _pick,
    _reactions_total,
    _replies_count,
    _to_int,
    message_to_item,
)


class PickTest(unittest.TestCase):
    def test_first_present_key(self):
        self.assertEqual(_pick({"a": 1, "b": 2}, "a", "b"), 1)
        self.assertEqual(_pick({"b": 2}, "a", "b"), 2)

    def test_skips_none_and_empty(self):
        self.assertEqual(_pick({"a": None, "b": "", "c": 5}, "a", "b", "c"), 5)

    def test_default(self):
        self.assertEqual(_pick({}, "a", default=42), 42)


class ToIntTest(unittest.TestCase):
    def test_int_passthrough(self):
        self.assertEqual(_to_int(42), 42)

    def test_float_truncates(self):
        self.assertEqual(_to_int(3.7), 3)

    def test_string_int(self):
        self.assertEqual(_to_int("100"), 100)

    def test_none(self):
        self.assertIsNone(_to_int(None))

    def test_dict_returns_none(self):
        # Critical: a stray dict like {"count": 100} from a different actor schema
        # must not leak into media as a number.
        self.assertIsNone(_to_int({"count": 100}))

    def test_bool_returns_none(self):
        # Avoid True being silently treated as 1
        self.assertIsNone(_to_int(True))

    def test_garbage_string(self):
        self.assertIsNone(_to_int("not a number"))


class RepliesCountTest(unittest.TestCase):
    def test_int_replies(self):
        # Critical: many actors return 'replies: 42' (just a number)
        self.assertEqual(_replies_count({"replies": 42}), 42)

    def test_list_replies_uses_length(self):
        self.assertEqual(_replies_count({"replies": [{"id": 1}, {"id": 2}]}), 2)

    def test_alt_keys(self):
        self.assertEqual(_replies_count({"repliesCount": 7}), 7)
        self.assertEqual(_replies_count({"replies_count": 8}), 8)
        self.assertEqual(_replies_count({"commentsCount": 9}), 9)

    def test_missing(self):
        self.assertIsNone(_replies_count({}))


class ReactionsTotalTest(unittest.TestCase):
    def test_list_of_dicts(self):
        self.assertEqual(
            _reactions_total([{"emoji": "👍", "count": 5}, {"emoji": "❤️", "count": 3}]),
            8,
        )

    def test_dict_emoji_to_count(self):
        self.assertEqual(_reactions_total({"👍": 5, "❤️": 3}), 8)

    def test_int_passthrough(self):
        self.assertEqual(_reactions_total(42), 42)

    def test_none(self):
        self.assertIsNone(_reactions_total(None))

    def test_empty_list(self):
        self.assertIsNone(_reactions_total([]))


SAMPLE_MESSAGE = {
    "id": 123,
    "channelUsername": "durov",
    "channelTitle": "Pavel Durov",
    "channelId": "-1001",
    "date": "2024-01-15T10:00:00.000Z",
    "text": "Big news today\nDetails inside",
    "url": "https://t.me/durov/123",
    "views": 500_000,
    "forwards": 1234,
    "replies": 89,
    "reactions": [
        {"emoji": "👍", "count": 1000},
        {"emoji": "❤️", "count": 500},
    ],
    "media": {"type": "photo", "url": "https://cdn.example/p.jpg"},
    "isPinned": True,
}


class MessageToItemTest(unittest.TestCase):
    def test_basic_fields(self):
        item = message_to_item(SAMPLE_MESSAGE)
        self.assertEqual(item.source, "telegram")
        self.assertEqual(item.item_id, "durov_123")
        self.assertEqual(item.url, "https://t.me/durov/123")
        self.assertEqual(item.title, "Big news today")
        self.assertEqual(item.author, "Pavel Durov")
        self.assertEqual(item.author_id, "durov")
        self.assertIn("Big news today", item.text)

    def test_metrics(self):
        item = message_to_item(SAMPLE_MESSAGE)
        self.assertEqual(item.media["views_count"], 500_000)
        self.assertEqual(item.media["forwards_count"], 1234)
        self.assertEqual(item.media["reactions_count"], 1500)
        self.assertEqual(item.media["media_type"], "photo")
        self.assertTrue(item.media["is_pinned"])

    def test_zero_views_kept(self):
        # 0 is a meaningful signal (just-published or banned), not a missing value
        msg = dict(SAMPLE_MESSAGE)
        msg["views"] = 0
        item = message_to_item(msg)
        self.assertEqual(item.media["views_count"], 0)

    def test_falls_back_to_alternative_field_names(self):
        msg = {
            "messageId": 99,
            "chatUsername": "somechan",
            "chatTitle": "Some Channel",
            "timestamp": "2024-02-01T00:00:00Z",
            "message": "alt format text",
            "view_count": 100,
            "forward_count": 5,
        }
        item = message_to_item(msg)
        self.assertEqual(item.item_id, "somechan_99")
        self.assertEqual(item.author, "Some Channel")
        self.assertEqual(item.author_id, "somechan")
        self.assertEqual(item.text, "alt format text")
        self.assertEqual(item.media["views_count"], 100)
        self.assertEqual(item.media["forwards_count"], 5)

    def test_url_constructed_when_missing(self):
        msg = dict(SAMPLE_MESSAGE)
        del msg["url"]
        item = message_to_item(msg)
        self.assertEqual(item.url, "https://t.me/durov/123")

    def test_raises_on_missing_id(self):
        with self.assertRaises(ValueError):
            message_to_item({"channelUsername": "durov", "text": "x"})

    def test_no_username_falls_back_to_id_only(self):
        msg = {"id": 123, "text": "x"}
        item = message_to_item(msg)
        self.assertEqual(item.item_id, "123")

    def test_extracts_inline_comments(self):
        msg = dict(SAMPLE_MESSAGE)
        msg["replies_data"] = [
            {
                "id": 1,
                "from": {"username": "fan1", "first_name": "Fan", "last_name": "One"},
                "text": "great post",
                "date": "2024-01-15T11:00:00Z",
                "reactions": [{"emoji": "👍", "count": 5}],
            },
            {
                "id": 2,
                "from": {"username": "fan2"},
                "text": "agreed",
                "date": "2024-01-15T11:30:00Z",
            },
        ]
        item = message_to_item(msg)
        self.assertEqual(len(item.comments), 2)
        c1, c2 = item.comments
        self.assertEqual(c1.text, "great post")
        self.assertEqual(c1.author, "Fan One")
        self.assertEqual(c1.author_id, "fan1")
        self.assertEqual(c1.like_count, 5)
        self.assertEqual(c2.author, "fan2")  # name fallback to username

    def test_no_comments_when_replies_is_int(self):
        # Some actors return 'replies' as an int count, not a list.
        msg = dict(SAMPLE_MESSAGE)
        msg["replies_data"] = 42
        item = message_to_item(msg)
        self.assertEqual(item.comments, [])

    def test_replies_int_populates_comments_count(self):
        # 'replies: 42' (no comment list) → comments_count=42, comments=[]
        msg = dict(SAMPLE_MESSAGE)
        msg["replies"] = 42
        # remove the explicit replies_data so we hit the int path
        msg.pop("replies_data", None)
        item = message_to_item(msg)
        self.assertEqual(item.media["comments_count"], 42)
        self.assertEqual(item.comments, [])

    def test_replies_as_list_populates_both(self):
        msg = dict(SAMPLE_MESSAGE)
        msg["replies"] = [
            {"id": 1, "from": {"username": "u1"}, "text": "x"},
            {"id": 2, "from": {"username": "u2"}, "text": "y"},
        ]
        msg.pop("replies_data", None)
        item = message_to_item(msg)
        self.assertEqual(item.media["comments_count"], 2)
        self.assertEqual(len(item.comments), 2)

    def test_dict_views_does_not_leak(self):
        # Defensive: if an actor returns views as a dict (unusual), do NOT
        # silently store it in media — coerce to None.
        msg = dict(SAMPLE_MESSAGE)
        msg["views"] = {"count": 999}
        item = message_to_item(msg)
        self.assertNotIn("views_count", item.media)

    def test_reply_tree_populates_parent_id(self):
        msg = dict(SAMPLE_MESSAGE)
        msg["replies_data"] = [
            {"id": 1, "from": {"username": "u1"}, "text": "top"},
            {"id": 2, "from": {"username": "u2"}, "text": "reply", "reply_to_message_id": 1},
            {"id": 3, "from": {"username": "u3"}, "text": "reply2", "replyToMessageId": 1},
        ]
        item = message_to_item(msg)
        ids_to_parent = {c.comment_id: c.parent_id for c in item.comments}
        self.assertIsNone(ids_to_parent["1"])
        self.assertEqual(ids_to_parent["2"], "1")
        self.assertEqual(ids_to_parent["3"], "1")

    def test_reply_to_outside_batch_stays_top_level(self):
        # If reply_to_message_id points to a message NOT in this batch,
        # treat as top-level (we have no context to chain it to).
        msg = dict(SAMPLE_MESSAGE)
        msg["replies_data"] = [
            {"id": 5, "from": {"username": "u"}, "text": "x", "reply_to_message_id": 99999},
        ]
        item = message_to_item(msg)
        self.assertIsNone(item.comments[0].parent_id)


if __name__ == "__main__":
    unittest.main()
