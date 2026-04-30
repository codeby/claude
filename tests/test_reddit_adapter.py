"""Tests for content_parser.plugins.reddit.adapter — PRAW objects → Item/Comment."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from content_parser.plugins.reddit.adapter import (
    _author_str,
    _iso,
    comment_to_core,
    submission_to_item,
)


def _redditor(name: str | None) -> SimpleNamespace | None:
    if name is None:
        return None
    return SimpleNamespace(name=name)


def _subreddit(name: str) -> SimpleNamespace:
    return SimpleNamespace(display_name=name)


def _make_submission(**overrides):
    base = dict(
        id="abc123",
        title="Some Post",
        author=_redditor("alice"),
        author_fullname="t2_111",
        created_utc=1_700_000_000.0,
        permalink="/r/python/comments/abc123/some_post/",
        url="https://reddit.com/r/python/comments/abc123/some_post/",
        is_self=True,
        selftext="Body text.",
        score=1234,
        upvote_ratio=0.95,
        num_comments=88,
        num_crossposts=2,
        subreddit=_subreddit("python"),
        link_flair_text="Discussion",
        is_video=False,
        over_18=False,
        spoiler=False,
        locked=False,
        domain="self.python",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class IsoTest(unittest.TestCase):
    def test_iso(self):
        self.assertTrue(_iso(1_700_000_000.0).startswith("2023-"))

    def test_iso_none(self):
        self.assertIsNone(_iso(None))


class AuthorTest(unittest.TestCase):
    def test_redditor_object(self):
        self.assertEqual(_author_str(_redditor("bob")), "bob")

    def test_deleted(self):
        self.assertEqual(_author_str(None), "[deleted]")


class SubmissionToItemTest(unittest.TestCase):
    def test_self_post_basic_fields(self):
        item = submission_to_item(_make_submission())
        self.assertEqual(item.source, "reddit")
        self.assertEqual(item.item_id, "abc123")
        self.assertEqual(item.title, "Some Post")
        self.assertEqual(item.author, "alice")
        self.assertEqual(item.author_id, "t2_111")
        self.assertEqual(item.url, "https://reddit.com/r/python/comments/abc123/some_post/")
        self.assertEqual(item.text, "Body text.")
        self.assertTrue(item.published_at.startswith("2023-"))

    def test_self_post_metrics(self):
        item = submission_to_item(_make_submission())
        self.assertEqual(item.media["score"], 1234)
        self.assertEqual(item.media["upvote_ratio"], 0.95)
        self.assertEqual(item.media["num_comments"], 88)
        self.assertEqual(item.media["subreddit"], "python")
        self.assertEqual(item.media["flair"], "Discussion")
        self.assertTrue(item.media["is_self"])

    def test_self_post_does_not_set_url_external(self):
        item = submission_to_item(_make_submission())
        self.assertNotIn("url_external", item.media)

    def test_link_post_sets_url_external(self):
        sub = _make_submission(
            is_self=False,
            selftext="",
            url="https://example.com/article",
            domain="example.com",
        )
        item = submission_to_item(sub)
        self.assertEqual(item.media["url_external"], "https://example.com/article")
        self.assertFalse(item.media["is_self"])
        self.assertIsNone(item.text)

    def test_deleted_author(self):
        item = submission_to_item(_make_submission(author=None))
        self.assertEqual(item.author, "[deleted]")

    def test_nsfw_and_locked_flags(self):
        item = submission_to_item(_make_submission(over_18=True, locked=True, spoiler=True))
        self.assertTrue(item.media["over_18"])
        self.assertTrue(item.media["locked"])
        self.assertTrue(item.media["spoiler"])

    def test_strips_none_metrics(self):
        sub = _make_submission()
        sub.upvote_ratio = None  # type: ignore[attr-defined]
        item = submission_to_item(sub)
        self.assertNotIn("upvote_ratio", item.media)

    def test_url_from_permalink_when_external_missing(self):
        item = submission_to_item(_make_submission(url=None, is_self=True))
        self.assertEqual(item.url, "https://reddit.com/r/python/comments/abc123/some_post/")

    def test_awards_in_extra(self):
        sub = _make_submission(all_awardings=[{"name": "Gold", "count": 2}])
        item = submission_to_item(sub)
        self.assertEqual(item.extra["awards"], [{"name": "Gold", "count": 2}])


class CommentToCoreTest(unittest.TestCase):
    def test_basic(self):
        c = SimpleNamespace(
            id="cmt1",
            author=_redditor("bob"),
            author_fullname="t2_222",
            body="great post",
            score=42,
            created_utc=1_700_000_500.0,
        )
        out = comment_to_core(c, parent_id=None)
        self.assertEqual(out.comment_id, "cmt1")
        self.assertIsNone(out.parent_id)
        self.assertEqual(out.author, "bob")
        self.assertEqual(out.author_id, "t2_222")
        self.assertEqual(out.text, "great post")
        self.assertEqual(out.like_count, 42)
        self.assertTrue(out.published_at.startswith("2023-"))

    def test_reply_carries_parent_id(self):
        c = SimpleNamespace(id="cmt2", author=None, body="ok", score=0, created_utc=0)
        out = comment_to_core(c, parent_id="cmt1")
        self.assertEqual(out.parent_id, "cmt1")
        self.assertEqual(out.author, "[deleted]")


if __name__ == "__main__":
    unittest.main()
