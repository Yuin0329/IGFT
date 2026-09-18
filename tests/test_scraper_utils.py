"""Tests for conservative Instagram profile URL parsing."""

from __future__ import annotations

import unittest

try:
    from scraper import (
        RELATIONSHIP_TEXT_PATTERNS,
        extract_username_from_profile_url,
        is_unavailable_profile_text,
        parse_relationship_count,
    )
except ModuleNotFoundError as exc:
    if exc.name != "playwright":
        raise
    extract_username_from_profile_url = None  # type: ignore[assignment]
    RELATIONSHIP_TEXT_PATTERNS = None  # type: ignore[assignment]
    parse_relationship_count = None  # type: ignore[assignment]
    is_unavailable_profile_text = None  # type: ignore[assignment]


@unittest.skipIf(extract_username_from_profile_url is None, "Playwright is not installed")
class ProfileUrlTests(unittest.TestCase):
    def test_accepts_direct_profile_urls(self) -> None:
        assert extract_username_from_profile_url is not None
        self.assertEqual(extract_username_from_profile_url("/Some.User/"), "some.user")
        self.assertEqual(
            extract_username_from_profile_url("https://www.instagram.com/ABC_123/"),
            "abc_123",
        )

    def test_rejects_non_profile_routes_and_external_urls(self) -> None:
        assert extract_username_from_profile_url is not None
        for url in (
            "/explore/",
            "/accounts/login/",
            "/reels/",
            "/direct/inbox/",
            "/p/shortcode/",
            "https://example.com/alice/",
        ):
            with self.subTest(url=url):
                self.assertIsNone(extract_username_from_profile_url(url))

    def test_relationship_text_fallback_supports_english_and_chinese(self) -> None:
        assert RELATIONSHIP_TEXT_PATTERNS is not None
        self.assertIsNotNone(RELATIONSHIP_TEXT_PATTERNS["followers"].search("154位粉絲"))
        self.assertIsNotNone(RELATIONSHIP_TEXT_PATTERNS["followers"].search("154 followers"))
        self.assertIsNotNone(RELATIONSHIP_TEXT_PATTERNS["following"].search("390追蹤中"))
        self.assertIsNotNone(RELATIONSHIP_TEXT_PATTERNS["following"].search("390 following"))

    def test_parses_relationship_counts(self) -> None:
        assert parse_relationship_count is not None
        self.assertEqual(parse_relationship_count("390 following"), 390)
        self.assertEqual(parse_relationship_count("1,234 followers"), 1234)
        self.assertEqual(parse_relationship_count("1.2K followers"), 1200)
        self.assertIsNone(parse_relationship_count("following"))

    def test_detects_unavailable_profile_messages(self) -> None:
        assert is_unavailable_profile_text is not None
        self.assertTrue(
            is_unavailable_profile_text("很抱歉，此頁面無法使用。 返回 Instagram。")
        )
        self.assertTrue(
            is_unavailable_profile_text("Sorry, this page isn't available.")
        )
        self.assertFalse(is_unavailable_profile_text("154 followers 390 following"))


if __name__ == "__main__":
    unittest.main()
