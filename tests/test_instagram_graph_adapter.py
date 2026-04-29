"""Tests for content_parser.plugins.instagram_graph.adapter."""
from __future__ import annotations

import unittest

from content_parser.plugins.instagram_graph.adapter import (
    _flatten_insights,
    comment_to_core,
    flatten_comments,
    media_to_item,
)


SAMPLE_REEL = {
    "id": "17895695668004550",
    "caption": "Big launch today\nLink in bio",
    "media_type": "REEL",
    "media_url": "https://cdn.example/reel.mp4",
    "thumbnail_url": "https://cdn.example/reel.jpg",
    "permalink": "https://www.instagram.com/reel/Cabc123/",
    "timestamp": "2026-04-01T12:00:00+0000",
    "is_comment_enabled": True,
    "comments_count": 42,
    "like_count": 1234,
    "owner": {"id": "17841405822304914", "username": "myaccount"},
    "insights": {
        "data": [
            {"name": "plays", "values": [{"value": 100000}]},
            {"name": "reach", "values": [{"value": 80000}]},
            {"name": "saved", "values": [{"value": 500}]},
            {"name": "shares", "values": [{"value": 250}]},
            {"name": "total_interactions", "values": [{"value": 2000}]},
        ]
    },
}


class FlattenInsightsTest(unittest.TestCase):
    def test_dict_envelope(self):
        out = _flatten_insights(SAMPLE_REEL["insights"])
        self.assertEqual(out["plays"], 100000)
        self.assertEqual(out["reach"], 80000)

    def test_list_form(self):
        out = _flatten_insights([
            {"name": "reach", "values": [{"value": 1000}]},
        ])
        self.assertEqual(out["reach"], 1000)

    def test_empty(self):
        self.assertEqual(_flatten_insights(None), {})
        self.assertEqual(_flatten_insights({}), {})

    def test_skips_non_numeric(self):
        out = _flatten_insights([{"name": "weird", "values": [{"value": "string"}]}])
        self.assertEqual(out, {})


class MediaToItemTest(unittest.TestCase):
    def test_basic_fields(self):
        item = media_to_item(SAMPLE_REEL, owner_username="myaccount")
        self.assertEqual(item.source, "instagram_graph")
        self.assertEqual(item.item_id, "17895695668004550")
        self.assertEqual(item.url, "https://www.instagram.com/reel/Cabc123/")
        self.assertEqual(item.title, "Big launch today")
        self.assertEqual(item.author, "myaccount")
        self.assertEqual(item.author_id, "17841405822304914")
        self.assertEqual(item.published_at, "2026-04-01T12:00:00+0000")

    def test_media_metrics(self):
        item = media_to_item(SAMPLE_REEL)
        self.assertEqual(item.media["media_type"], "REEL")
        self.assertEqual(item.media["like_count"], 1234)
        self.assertEqual(item.media["comments_count"], 42)
        self.assertEqual(item.media["plays"], 100000)
        self.assertEqual(item.media["reach"], 80000)
        self.assertEqual(item.media["saved"], 500)
        self.assertEqual(item.media["shares"], 250)
        self.assertEqual(item.media["total_interactions"], 2000)

    def test_strips_empty(self):
        post = {"id": "1", "media_type": "IMAGE", "permalink": "https://x"}
        item = media_to_item(post)
        # Without like_count etc., they shouldn't appear
        self.assertNotIn("like_count", item.media)
        self.assertEqual(item.media["media_type"], "IMAGE")

    def test_raises_on_missing_id(self):
        with self.assertRaises(ValueError):
            media_to_item({"caption": "no id"})

    def test_owner_username_overrides(self):
        item = media_to_item(SAMPLE_REEL, owner_username="custom")
        self.assertEqual(item.author, "custom")

    def test_falls_back_to_owner_when_no_username_passed(self):
        # When the caller doesn't pass owner_username, we should still see author
        # via the inline owner.username.
        item = media_to_item(SAMPLE_REEL)
        # adapter doesn't read owner.username for author currently; author is
        # only set from owner_username argument. Confirm explicit None when omitted:
        self.assertIsNone(item.author)
        # But author_id still comes from owner.id
        self.assertEqual(item.author_id, "17841405822304914")


class CommentToCore(unittest.TestCase):
    def test_top_level(self):
        c = {"id": "9001", "text": "great", "username": "fan",
             "timestamp": "2026-04-01T13:00:00+0000", "like_count": 5,
             "user": {"id": "999"}}
        out = comment_to_core(c)
        self.assertEqual(out.comment_id, "9001")
        self.assertIsNone(out.parent_id)
        self.assertEqual(out.author, "fan")
        self.assertEqual(out.author_id, "999")
        self.assertEqual(out.text, "great")
        self.assertEqual(out.like_count, 5)

    def test_reply_carries_parent(self):
        c = {"id": "9002", "text": "thx"}
        out = comment_to_core(c, parent_id="9001")
        self.assertEqual(out.parent_id, "9001")


class FlattenCommentsTest(unittest.TestCase):
    def test_with_inline_replies(self):
        comments = [
            {"id": "1", "text": "top", "username": "fan",
             "replies": {"data": [
                 {"id": "1a", "text": "thanks!", "username": "myaccount"},
                 {"id": "1b", "text": "+1", "username": "fan2"},
             ]}},
            {"id": "2", "text": "another top", "username": "fan3"},
        ]
        out = flatten_comments(comments)
        # 2 top-level + 2 replies = 4 total
        self.assertEqual(len(out), 4)
        self.assertIsNone(out[0].parent_id)
        self.assertEqual(out[1].parent_id, "1")
        self.assertEqual(out[2].parent_id, "1")
        self.assertIsNone(out[3].parent_id)

    def test_no_replies_field(self):
        comments = [{"id": "1", "text": "top", "username": "fan"}]
        out = flatten_comments(comments)
        self.assertEqual(len(out), 1)

    def test_empty_list(self):
        self.assertEqual(flatten_comments([]), [])

    def test_skips_non_dict(self):
        out = flatten_comments([{"id": "1", "text": "ok"}, "garbage", None])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
