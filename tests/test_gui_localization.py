"""Tests for bilingual GUI output formatting."""

from __future__ import annotations

import unittest

from gui import (
    LANGUAGE_NAMES,
    _format_change_set,
    _format_relationship_summary,
    _state_label,
)
from models import ChangeSet, RelationshipAnalysis


class GuiLocalizationTests(unittest.TestCase):
    def test_english_is_the_default_language(self) -> None:
        self.assertEqual(LANGUAGE_NAMES["en"], "English")
        analysis = RelationshipAnalysis(
            mutual=frozenset({"alice"}),
            not_following_back=frozenset({"bob"}),
            i_dont_follow_back=frozenset({"carol"}),
        )

        result = _format_relationship_summary(analysis)

        self.assertIn("Current relationships", result)
        self.assertIn("Mutual: 1", result)
        self.assertNotIn("目前關係", result)

    def test_traditional_chinese_output(self) -> None:
        changes = ChangeSet(
            unfollowed_me=frozenset({"alice"}),
            new_followers=frozenset({"bob"}),
            i_unfollowed=frozenset(),
            i_followed=frozenset({"carol"}),
        )

        result = _format_change_set(changes, "zh_TW")

        self.assertIn("取消追蹤你：1", result)
        self.assertIn("新的 Followers：1", result)
        self.assertIn("你新追蹤：1", result)

    def test_relationship_state_labels_are_localized(self) -> None:
        self.assertEqual(_state_label(True, True), "Mutual")
        self.assertEqual(_state_label(True, True, "zh_TW"), "互追")


if __name__ == "__main__":
    unittest.main()
