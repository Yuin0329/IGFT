"""Unit tests for pure relationship analysis and validation."""

from __future__ import annotations

import unittest

from analyzer import analyze_relationships, compare_snapshots, validate_scan_counts


class AnalyzerTests(unittest.TestCase):
    def test_relationship_analysis(self) -> None:
        result = analyze_relationships({"alice", "bob"}, {"bob", "carol"})
        self.assertEqual(result.mutual, frozenset({"bob"}))
        self.assertEqual(result.not_following_back, frozenset({"carol"}))
        self.assertEqual(result.i_dont_follow_back, frozenset({"alice"}))

    def test_snapshot_changes(self) -> None:
        result = compare_snapshots(
            old_followers={"alice", "bob"},
            old_following={"alice", "carol"},
            current_followers={"bob", "dave"},
            current_following={"carol", "dave"},
        )
        self.assertEqual(result.unfollowed_me, frozenset({"alice"}))
        self.assertEqual(result.new_followers, frozenset({"dave"}))
        self.assertEqual(result.i_unfollowed, frozenset({"alice"}))
        self.assertEqual(result.i_followed, frozenset({"dave"}))

    def test_validation_checks_lists_independently(self) -> None:
        issues = validate_scan_counts(100, 200, 69, 150, 0.70)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].relationship_name, "followers")


if __name__ == "__main__":
    unittest.main()

