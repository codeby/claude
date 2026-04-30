"""Tests for content_parser.plugins.instagram.adapter — Apify post → Item."""
from __future__ import annotations

import unittest

from content_parser.plugins.instagram.adapter import post_to_item


SAMPLE_REEL = {
    "id": "9876",
    "shortCode": "C_short",
    "url": "https://www.instagram.com/reel/C_short/",
    "type": "Video",
    "caption": "Check this out\n#fun #fyp",
    "ownerUsername": "creator",
    "ownerId": 4242,
    "timestamp": "2026-04-01T12:00:00.000Z",
    "likesCount": 12345,
    "commentsCount": 78,
    "videoViewCount": 1_500_000,
    "videoUrl": "https://cdn.example/v.mp4",
    "displayUrl": "https://cdn.example/t.jpg",
    "videoDuration": 17.5,
    "musicInfo": {
        "audio_id": "snd_42",
        "song_name": "Song Name",
        "artist_name": "Artist Name",
    },
    "hashtags": ["fun", "fyp"],
    "latestComments": [
        {
            "id": "c1",
            "ownerUsername": "fan1",
            "text": "🔥",
            "likesCount": 10,
            "timestamp": "2026-04-01T13:00:00Z",
            "replies": [
                {"id": "c1r1", "ownerUsername": "creator", "text": "thanks!", "likesCount": 2},
            ],
        },
        {"id": "c2", "ownerUsername": "hater", "text": "meh"},
    ],
    "locationName": "Somewhere",
}


class AdapterTest(unittest.TestCase):
    def test_basic_fields(self):
        item = post_to_item(SAMPLE_REEL)
        self.assertEqual(item.source, "instagram")
        self.assertEqual(item.item_id, "C_short")
        self.assertEqual(item.url, "https://www.instagram.com/reel/C_short/")
        self.assertEqual(item.author, "creator")
        self.assertEqual(item.author_id, "4242")
        self.assertEqual(item.title, "Check this out")  # caption first line, not full caption
        self.assertEqual(item.text, "Check this out\n#fun #fyp")  # full caption preserved

    def test_metrics(self):
        item = post_to_item(SAMPLE_REEL)
        self.assertEqual(item.media["like_count"], 12345)
        self.assertEqual(item.media["comment_count"], 78)
        self.assertEqual(item.media["view_count"], 1_500_000)
        self.assertEqual(item.media["video_duration"], 17.5)
        self.assertEqual(item.media["audio_id"], "snd_42")
        self.assertEqual(item.media["audio_title"], "Song Name")
        self.assertEqual(item.media["audio_artist"], "Artist Name")

    def test_extras(self):
        item = post_to_item(SAMPLE_REEL)
        self.assertEqual(item.extra["hashtags"], ["fun", "fyp"])
        self.assertEqual(item.extra["location_name"], "Somewhere")

    def test_comments_flattened_with_parent_link(self):
        item = post_to_item(SAMPLE_REEL)
        # 2 top-level + 1 reply = 3 total, in order: top1, reply, top2
        self.assertEqual(len(item.comments), 3)
        top1, reply, top2 = item.comments
        self.assertIsNone(top1.parent_id)
        self.assertEqual(top1.author, "fan1")
        self.assertEqual(reply.parent_id, "c1")
        self.assertEqual(reply.author, "creator")
        self.assertIsNone(top2.parent_id)
        self.assertEqual(top2.author, "hater")

    def test_image_post_no_video_metrics(self):
        post = {
            "id": "1", "shortCode": "IMG", "url": "https://insta/p/IMG/",
            "type": "Image", "caption": "static",
            "ownerUsername": "x", "timestamp": "2026-04-01T00:00:00Z",
            "likesCount": 100, "commentsCount": 0,
        }
        item = post_to_item(post)
        self.assertEqual(item.media["like_count"], 100)
        self.assertNotIn("view_count", item.media)
        self.assertNotIn("audio_id", item.media)
        self.assertEqual(item.comments, [])

    def test_falls_back_to_id_when_no_shortcode(self):
        post = {"id": "abc", "ownerUsername": "x"}
        item = post_to_item(post)
        self.assertEqual(item.item_id, "abc")

    def test_handles_empty_caption(self):
        post = {"id": "1", "shortCode": "X"}
        item = post_to_item(post)
        self.assertIsNone(item.title)


if __name__ == "__main__":
    unittest.main()
