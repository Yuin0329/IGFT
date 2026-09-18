"""Integration smoke tests for SQLite snapshot and event persistence."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import ConcurrentSnapshotError, TrackerDatabase
from models import Account, EventType, NonFollowerStatus


def account(username: str) -> Account:
    return Account(
        username=username,
        profile_url=f"https://www.instagram.com/{username}/",
    )


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database = TrackerDatabase(Path(self.temp_directory.name) / "tracker.db")
        self.database.initialize()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_snapshots_events_and_nonfollower_history(self) -> None:
        first_time = datetime(2026, 9, 18, 1, 0, tzinfo=timezone.utc)
        first = self.database.commit_snapshot(
            followers={"alice": account("alice")},
            following={"alice": account("alice"), "bob": account("bob")},
            expected_previous_id=None,
            created_at=first_time,
        )
        self.assertIsNone(first.previous)
        self.assertFalse(first.changes.new_followers)
        self.assertFalse(self.database.get_changes_for_snapshot(first.current.id).new_followers)

        second = self.database.commit_snapshot(
            followers={"carol": account("carol")},
            following={
                "alice": account("alice"),
                "bob": account("bob"),
                "dave": account("dave"),
            },
            expected_previous_id=first.current.id,
            created_at=first_time + timedelta(days=1),
        )
        self.assertEqual(second.changes.unfollowed_me, frozenset({"alice"}))
        self.assertEqual(second.changes.new_followers, frozenset({"carol"}))
        self.assertEqual(second.changes.i_followed, frozenset({"dave"}))

        persisted = self.database.get_changes_for_snapshot(second.current.id)
        self.assertEqual(persisted, second.changes)

        details = {
            detail.username: detail
            for detail in self.database.get_nonfollowers_details(second.current.id)
        }
        self.assertEqual(details["alice"].status, NonFollowerStatus.UNFOLLOWED_YOU)
        self.assertEqual(details["alice"].last_mutual_at, first_time)
        self.assertEqual(
            details["alice"].detected_unfollow_at,
            first_time + timedelta(days=1),
        )
        self.assertEqual(details["bob"].status, NonFollowerStatus.NEVER_FOLLOWED_BACK)
        self.assertIsNone(details["bob"].last_mutual_at)

        history = self.database.get_account_history("@ALICE")
        self.assertIsNotNone(history)
        assert history is not None
        self.assertEqual(len(history.states), 2)
        self.assertTrue(history.states[0].is_follower)
        self.assertFalse(history.states[1].is_follower)
        self.assertIn(EventType.UNFOLLOWED_ME, {event.event_type for event in history.events})

        with self.assertRaises(ConcurrentSnapshotError):
            self.database.commit_snapshot(
                followers={},
                following={},
                expected_previous_id=first.current.id,
                created_at=first_time + timedelta(days=2),
            )
        latest = self.database.get_latest_snapshot()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.id, second.current.id)


if __name__ == "__main__":
    unittest.main()
