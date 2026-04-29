"""Tests for content_parser.plugins.reddit.plugin — input validation + dispatch."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from content_parser.core.errors import PluginError
from content_parser.plugins.reddit.plugin import RedditPlugin


class NormalizeSubredditTest(unittest.TestCase):
    def setUp(self):
        self.p = RedditPlugin()

    def test_plain_name(self):
        self.assertEqual(self.p._normalize_subreddit("python"), "python")

    def test_strips_r_prefix(self):
        self.assertEqual(self.p._normalize_subreddit("r/python"), "python")
        self.assertEqual(self.p._normalize_subreddit("R/Python"), "Python")

    def test_url(self):
        self.assertEqual(
            self.p._normalize_subreddit("https://reddit.com/r/MachineLearning/"),
            "MachineLearning",
        )

    def test_rejects_bad_chars(self):
        with self.assertRaises(PluginError):
            self.p._normalize_subreddit("not a sub!")

    def test_rejects_user_url(self):
        with self.assertRaises(PluginError):
            self.p._normalize_subreddit("https://reddit.com/u/spez/")


class NormalizeUserTest(unittest.TestCase):
    def setUp(self):
        self.p = RedditPlugin()

    def test_plain(self):
        self.assertEqual(self.p._normalize_user("spez"), "spez")

    def test_u_prefix(self):
        self.assertEqual(self.p._normalize_user("/u/spez"), "spez")
        self.assertEqual(self.p._normalize_user("u/spez"), "spez")
        self.assertEqual(self.p._normalize_user("/user/spez"), "spez")

    def test_url(self):
        self.assertEqual(self.p._normalize_user("https://reddit.com/u/spez/"), "spez")

    def test_at_prefix(self):
        self.assertEqual(self.p._normalize_user("@spez"), "spez")

    def test_rejects_too_short(self):
        with self.assertRaises(PluginError):
            self.p._normalize_user("ab")


class IsRedditPostUrlTest(unittest.TestCase):
    def setUp(self):
        self.p = RedditPlugin()

    def test_canonical_post_url(self):
        self.assertTrue(self.p._is_reddit_post_url(
            "https://www.reddit.com/r/python/comments/abc/title/"
        ))

    def test_subreddit_listing_is_not_post(self):
        self.assertFalse(self.p._is_reddit_post_url("https://reddit.com/r/python/"))

    def test_other_host_rejected(self):
        self.assertFalse(self.p._is_reddit_post_url(
            "https://example.com/r/python/comments/abc/title/"
        ))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.p = RedditPlugin()

    def test_specs_carry_kind_prefix(self):
        specs = self.p.resolve(
            {
                "subreddit": ["python", "r/MachineLearning"],
                "query": ["claude code"],
                "post_url": ["https://www.reddit.com/r/python/comments/abc/title/"],
                "user": ["/u/spez"],
            },
            {},
            {"REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "y"},
        )
        kinds = [s.split(":", 1)[0] for s in specs]
        self.assertEqual(kinds.count("subreddit"), 2)
        self.assertEqual(kinds.count("query"), 1)
        self.assertEqual(kinds.count("post_url"), 1)
        self.assertEqual(kinds.count("user"), 1)

    def test_dedupes(self):
        specs = self.p.resolve(
            {"subreddit": ["python", "r/python", "PYTHON"]},
            {},
            {"REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "y"},
        )
        # python and r/python normalize to same; PYTHON stays as-is (case-sensitive on Reddit).
        self.assertEqual(len(specs), 2)

    def test_rejects_listing_url_in_post_field(self):
        with self.assertRaises(PluginError):
            self.p.resolve(
                {"post_url": ["https://reddit.com/r/python/"]},
                {},
                {"REDDIT_CLIENT_ID": "x", "REDDIT_CLIENT_SECRET": "y"},
            )


class CommentCollectionTest(unittest.TestCase):
    """Verify _collect_comments respects depth + max_comments + expand_more."""

    def setUp(self):
        self.p = RedditPlugin()

    def _comment(self, id, score=10, replies=None):
        return SimpleNamespace(
            id=id,
            author=SimpleNamespace(name=f"u_{id}"),
            author_fullname=f"t2_{id}",
            body=f"text {id}",
            score=score,
            created_utc=1_700_000_000.0,
            replies=replies or [],
        )

    def _submission_with_comments(self, comments, expand_called):
        comments_obj = MagicMock()
        comments_obj.__iter__ = lambda self_: iter(comments)
        def replace_more(limit):
            expand_called.append(limit)
        comments_obj.replace_more = replace_more
        return SimpleNamespace(comments=comments_obj)

    def test_top_level_only(self):
        replies = [self._comment("r1"), self._comment("r2")]
        top = [self._comment("c1", replies=replies), self._comment("c2")]
        called = []
        sub = self._submission_with_comments(top, called)

        out = self.p._collect_comments(sub, max_comments=100, depth="top_level", expand_more=False)
        ids = [c.comment_id for c in out]
        self.assertEqual(ids, ["c1", "c2"])
        self.assertEqual(called, [0])  # replace_more(limit=0) when expand_more=False

    def test_all_depth_walks_replies(self):
        replies = [self._comment("r1"), self._comment("r2")]
        top = [self._comment("c1", replies=replies), self._comment("c2")]
        called = []
        sub = self._submission_with_comments(top, called)

        out = self.p._collect_comments(sub, max_comments=100, depth="all", expand_more=False)
        ids = [c.comment_id for c in out]
        self.assertEqual(ids, ["c1", "r1", "r2", "c2"])
        # parent_id linkage
        self.assertIsNone(out[0].parent_id)  # c1
        self.assertEqual(out[1].parent_id, "c1")  # r1 under c1
        self.assertEqual(out[2].parent_id, "c1")  # r2 under c1
        self.assertIsNone(out[3].parent_id)  # c2

    def test_max_comments_caps(self):
        top = [self._comment(f"c{i}") for i in range(10)]
        called = []
        sub = self._submission_with_comments(top, called)
        out = self.p._collect_comments(sub, max_comments=3, depth="top_level", expand_more=False)
        self.assertEqual(len(out), 3)

    def test_expand_more_true_passes_none(self):
        called = []
        sub = self._submission_with_comments([], called)
        self.p._collect_comments(sub, max_comments=100, depth="top_level", expand_more=True)
        self.assertEqual(called, [None])


class FetchAuthGuardTest(unittest.TestCase):
    """fetch() raises AuthError early without secrets."""

    def test_missing_client_id(self):
        from content_parser.core.errors import AuthError
        p = RedditPlugin()
        with self.assertRaises(AuthError):
            list(p.fetch(["subreddit:python"], {}, {"REDDIT_CLIENT_SECRET": "y"}))

    def test_missing_client_secret(self):
        from content_parser.core.errors import AuthError
        p = RedditPlugin()
        with self.assertRaises(AuthError):
            list(p.fetch(["subreddit:python"], {}, {"REDDIT_CLIENT_ID": "x"}))


class ListingDispatchTest(unittest.TestCase):
    """_collect_submissions chooses the right PRAW listing method."""

    def setUp(self):
        self.p = RedditPlugin()

    def _reddit(self):
        sub = MagicMock()
        sub.top.return_value = [SimpleNamespace(id="t1")]
        sub.hot.return_value = [SimpleNamespace(id="h1")]
        sub.new.return_value = [SimpleNamespace(id="n1")]
        sub.rising.return_value = [SimpleNamespace(id="ri1")]
        sub.controversial.return_value = [SimpleNamespace(id="cn1")]
        sub.search.return_value = [SimpleNamespace(id="s1")]
        reddit = MagicMock()
        reddit.subreddit.return_value = sub
        return reddit, sub

    def test_subreddit_top(self):
        reddit, sub = self._reddit()
        out = list(self.p._collect_submissions(reddit, "subreddit", "python", "top", "month", 5))
        sub.top.assert_called_once_with(time_filter="month", limit=5)
        self.assertEqual(out[0].id, "t1")

    def test_subreddit_new_does_not_pass_time_filter(self):
        reddit, sub = self._reddit()
        list(self.p._collect_submissions(reddit, "subreddit", "python", "new", "month", 5))
        sub.new.assert_called_once_with(limit=5)

    def test_query_uses_search_on_all(self):
        reddit, sub = self._reddit()
        list(self.p._collect_submissions(reddit, "query", "claude code", "top", "week", 5))
        reddit.subreddit.assert_called_with("all")
        sub.search.assert_called_once()

    def test_post_url_uses_submission(self):
        reddit, _ = self._reddit()
        reddit.submission.return_value = SimpleNamespace(id="p1")
        out = list(self.p._collect_submissions(
            reddit, "post_url", "https://reddit.com/r/x/comments/abc/", "top", "month", 5
        ))
        reddit.submission.assert_called_once_with(url="https://reddit.com/r/x/comments/abc/")
        self.assertEqual(out[0].id, "p1")


if __name__ == "__main__":
    unittest.main()
