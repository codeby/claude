"""Tests for content_parser.plugins.vk.adapter — VK dicts → Item/Comment."""
from __future__ import annotations

import unittest

from content_parser.plugins.vk.adapter import (
    _iso,
    _label_for_id,
    comment_to_core,
    index_by_id,
    post_to_item,
)


SAMPLE_POST = {
    "id": 678,
    "owner_id": -12345,           # negative = group
    "from_id": -12345,
    "date": 1_700_000_000,
    "post_type": "post",
    "text": "Запуск нового продукта\nПодробности по ссылке.",
    "attachments": [
        {"type": "photo", "photo": {}},
        {"type": "video", "video": {}},
    ],
    "comments": {"count": 42},
    "likes": {"count": 1234},
    "reposts": {"count": 56},
    "views": {"count": 100_000},
    "marked_as_ads": False,
    "is_pinned": True,
}


SAMPLE_PROFILES = [
    {"id": 555, "first_name": "Иван", "last_name": "Иванов", "screen_name": "ivanov"},
    {"id": 777, "first_name": "Петя", "last_name": "Петров"},
]
SAMPLE_GROUPS = [
    {"id": 12345, "name": "Awesome Group", "screen_name": "awesome"},
]


class IsoTest(unittest.TestCase):
    def test_unix_to_iso(self):
        self.assertTrue(_iso(1_700_000_000).startswith("2023-"))

    def test_none(self):
        self.assertIsNone(_iso(None))


class LabelForIdTest(unittest.TestCase):
    def test_user_id(self):
        profiles = index_by_id(SAMPLE_PROFILES)
        groups = index_by_id(SAMPLE_GROUPS)
        name, label = _label_for_id(555, profiles, groups)
        self.assertEqual(name, "Иван Иванов")
        self.assertEqual(label, "id555")

    def test_group_id_is_negative(self):
        profiles = index_by_id(SAMPLE_PROFILES)
        groups = index_by_id(SAMPLE_GROUPS)
        name, label = _label_for_id(-12345, profiles, groups)
        self.assertEqual(name, "Awesome Group")
        self.assertEqual(label, "club12345")

    def test_unknown_user_falls_back_to_id(self):
        name, label = _label_for_id(999, {}, {})
        self.assertEqual(name, "id999")
        self.assertEqual(label, "id999")


class PostToItemTest(unittest.TestCase):
    def test_basic_fields(self):
        item = post_to_item(
            SAMPLE_POST,
            profiles_by_id=index_by_id(SAMPLE_PROFILES),
            groups_by_id=index_by_id(SAMPLE_GROUPS),
        )
        self.assertEqual(item.source, "vk")
        self.assertEqual(item.item_id, "-12345_678")
        self.assertEqual(item.url, "https://vk.com/wall-12345_678")
        self.assertEqual(item.title, "Запуск нового продукта")
        self.assertEqual(item.author, "Awesome Group")
        self.assertEqual(item.author_id, "-12345")
        self.assertTrue(item.published_at.startswith("2023-"))
        self.assertIn("Запуск", item.text)

    def test_metrics(self):
        item = post_to_item(
            SAMPLE_POST,
            profiles_by_id=index_by_id(SAMPLE_PROFILES),
            groups_by_id=index_by_id(SAMPLE_GROUPS),
        )
        self.assertEqual(item.media["likes_count"], 1234)
        self.assertEqual(item.media["reposts_count"], 56)
        self.assertEqual(item.media["comments_count"], 42)
        self.assertEqual(item.media["views_count"], 100_000)
        self.assertTrue(item.media["has_photo"])
        self.assertTrue(item.media["has_video"])
        self.assertTrue(item.media["is_pinned"])
        # marked_as_ads is False → stripped from media
        self.assertNotIn("marked_as_ads", item.media)

    def test_attachment_types_in_extra(self):
        item = post_to_item(SAMPLE_POST)
        self.assertEqual(item.extra["attachment_types"], ["photo", "video"])

    def test_explicit_owner_label_wins(self):
        item = post_to_item(SAMPLE_POST, owner_label="Custom Label")
        self.assertEqual(item.author, "Custom Label")

    def test_empty_text_keeps_title_none(self):
        post = dict(SAMPLE_POST)
        post["text"] = ""
        item = post_to_item(post)
        self.assertIsNone(item.title)


class CommentToCoreTest(unittest.TestCase):
    def test_user_comment(self):
        c = {
            "id": 9001,
            "from_id": 555,
            "date": 1_700_000_500,
            "text": "Отличный пост",
            "likes": {"count": 7},
        }
        out = comment_to_core(
            c,
            parent_id=None,
            profiles_by_id=index_by_id(SAMPLE_PROFILES),
            groups_by_id=index_by_id(SAMPLE_GROUPS),
        )
        self.assertEqual(out.comment_id, "9001")
        self.assertIsNone(out.parent_id)
        self.assertEqual(out.author, "Иван Иванов")
        self.assertEqual(out.author_id, "id555")
        self.assertEqual(out.text, "Отличный пост")
        self.assertEqual(out.like_count, 7)

    def test_reply_carries_parent_id(self):
        c = {"id": 9002, "from_id": -12345, "date": 1_700_000_600, "text": "thx"}
        out = comment_to_core(
            c,
            parent_id="9001",
            profiles_by_id=index_by_id(SAMPLE_PROFILES),
            groups_by_id=index_by_id(SAMPLE_GROUPS),
        )
        self.assertEqual(out.parent_id, "9001")
        self.assertEqual(out.author, "Awesome Group")
        self.assertEqual(out.author_id, "club12345")


class IndexByIdTest(unittest.TestCase):
    def test_basic(self):
        idx = index_by_id([{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])
        self.assertEqual(idx[1]["name"], "a")
        self.assertEqual(idx[2]["name"], "b")

    def test_skips_bad_entries(self):
        idx = index_by_id([{"id": 1}, {}, {"id": "not-int"}])
        self.assertEqual(set(idx.keys()), {1})


if __name__ == "__main__":
    unittest.main()
