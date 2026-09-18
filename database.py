"""SQLite persistence for snapshots, accounts, relationships, and events."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from analyzer import compare_snapshots
from models import (
    Account,
    AccountHistory,
    AccountState,
    ChangeSet,
    EventRecord,
    EventType,
    NonFollowerDetail,
    NonFollowerStatus,
    RelationshipType,
    Snapshot,
    StoredScan,
    normalize_username,
)


LOGGER = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    followers_count INTEGER NOT NULL CHECK (followers_count >= 0),
    following_count INTEGER NOT NULL CHECK (following_count >= 0)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instagram_user_id TEXT UNIQUE,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    profile_url TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_accounts (
    snapshot_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    relationship_type TEXT NOT NULL
        CHECK (relationship_type IN ('follower', 'following')),
    PRIMARY KEY (snapshot_id, account_id, relationship_type),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL,
    detected_at TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('FOLLOWED_ME', 'UNFOLLOWED_ME', 'I_FOLLOWED', 'I_UNFOLLOWED')
    ),
    UNIQUE (snapshot_id, account_id, event_type),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username);
CREATE INDEX IF NOT EXISTS idx_snapshot_accounts_snapshot
    ON snapshot_accounts(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshot_accounts_account
    ON snapshot_accounts(account_id);
CREATE INDEX IF NOT EXISTS idx_events_snapshot ON events(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id);
CREATE INDEX IF NOT EXISTS idx_events_detected_at ON events(detected_at);
"""


class DatabaseError(RuntimeError):
    """Base exception for tracker persistence failures."""


class ConcurrentSnapshotError(DatabaseError):
    """Raised if another process saved a snapshot while a scan was running."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _to_storage_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _from_storage_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _snapshot_from_row(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=int(row["id"]),
        created_at=_from_storage_time(str(row["created_at"])),
        followers_count=int(row["followers_count"]),
        following_count=int(row["following_count"]),
    )


class TrackerDatabase:
    """Small, connection-per-operation SQLite repository."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        """Create the database directory and all tables/indexes when absent."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                connection.executescript(SCHEMA_SQL)
                connection.commit()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not initialize database: {exc}") from exc

    def get_latest_snapshot(self) -> Snapshot | None:
        """Return the newest snapshot, or None before the first scan."""

        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT id, created_at, followers_count, following_count
                    FROM snapshots
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load latest snapshot: {exc}") from exc
        return _snapshot_from_row(row) if row is not None else None

    def get_previous_snapshot(self, snapshot_id: int) -> Snapshot | None:
        """Return the snapshot immediately preceding the supplied snapshot."""

        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    """
                    SELECT id, created_at, followers_count, following_count
                    FROM snapshots
                    WHERE id < ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (snapshot_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load previous snapshot: {exc}") from exc
        return _snapshot_from_row(row) if row is not None else None

    def get_relationship_usernames(
        self, snapshot_id: int, relationship_type: RelationshipType
    ) -> frozenset[str]:
        """Load usernames for one relationship type at a snapshot."""

        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT a.username
                    FROM snapshot_accounts AS sa
                    JOIN accounts AS a ON a.id = sa.account_id
                    WHERE sa.snapshot_id = ? AND sa.relationship_type = ?
                    ORDER BY a.username COLLATE NOCASE
                    """,
                    (snapshot_id, relationship_type.value),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load snapshot accounts: {exc}") from exc
        return frozenset(str(row["username"]) for row in rows)

    def commit_snapshot(
        self,
        followers: Mapping[str, Account],
        following: Mapping[str, Account],
        expected_previous_id: int | None,
        created_at: datetime | None = None,
    ) -> StoredScan:
        """Atomically persist both lists, their snapshot, and derived events."""

        timestamp = created_at or _utc_now()
        stored_time = _to_storage_time(timestamp)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous, changes = self._prepare_changes_for_commit(
                connection,
                followers,
                following,
                expected_previous_id,
            )
            snapshot_id = self._insert_snapshot_row(
                connection, stored_time, len(followers), len(following)
            )
            account_ids = self._upsert_current_accounts(
                connection, followers, following, stored_time
            )
            self._insert_relationships(
                connection,
                snapshot_id,
                followers.keys(),
                account_ids,
                RelationshipType.FOLLOWER,
            )
            self._insert_relationships(
                connection,
                snapshot_id,
                following.keys(),
                account_ids,
                RelationshipType.FOLLOWING,
            )
            self._insert_events(connection, snapshot_id, stored_time, changes, account_ids)
            connection.commit()

            current = Snapshot(
                id=snapshot_id,
                created_at=_from_storage_time(stored_time),
                followers_count=len(followers),
                following_count=len(following),
            )
            LOGGER.info("Snapshot #%s saved", snapshot_id)
            return StoredScan(current=current, previous=previous, changes=changes)
        except ConcurrentSnapshotError:
            connection.rollback()
            raise
        except DatabaseError:
            connection.rollback()
            raise
        except (sqlite3.Error, KeyError, ValueError) as exc:
            connection.rollback()
            raise DatabaseError(f"Snapshot transaction failed and was rolled back: {exc}") from exc
        finally:
            connection.close()

    def _prepare_changes_for_commit(
        self,
        connection: sqlite3.Connection,
        followers: Mapping[str, Account],
        following: Mapping[str, Account],
        expected_previous_id: int | None,
    ) -> tuple[Snapshot | None, ChangeSet]:
        latest_row = connection.execute(
            """
            SELECT id, created_at, followers_count, following_count
            FROM snapshots ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        previous = _snapshot_from_row(latest_row) if latest_row is not None else None
        actual_previous_id = previous.id if previous is not None else None
        if actual_previous_id != expected_previous_id:
            raise ConcurrentSnapshotError(
                "Another tracker process saved a snapshot during this scan. "
                "No data from this scan was saved; run it again."
            )
        if previous is None:
            return None, ChangeSet()

        old_followers = self._relationship_names_in_connection(
            connection, actual_previous_id, RelationshipType.FOLLOWER
        )
        old_following = self._relationship_names_in_connection(
            connection, actual_previous_id, RelationshipType.FOLLOWING
        )
        return previous, compare_snapshots(
            old_followers,
            old_following,
            followers.keys(),
            following.keys(),
        )

    @staticmethod
    def _insert_snapshot_row(
        connection: sqlite3.Connection,
        stored_time: str,
        followers_count: int,
        following_count: int,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO snapshots(created_at, followers_count, following_count)
            VALUES (?, ?, ?)
            """,
            (stored_time, followers_count, following_count),
        )
        return int(cursor.lastrowid)

    def _upsert_current_accounts(
        self,
        connection: sqlite3.Connection,
        followers: Mapping[str, Account],
        following: Mapping[str, Account],
        stored_time: str,
    ) -> dict[str, int]:
        all_accounts = dict(following)
        all_accounts.update(followers)
        account_ids: dict[str, int] = {}
        for username, account in all_accounts.items():
            canonical_username = normalize_username(username)
            account_ids[canonical_username] = self._upsert_account(
                connection, account, stored_time
            )
        return account_ids

    @staticmethod
    def _relationship_names_in_connection(
        connection: sqlite3.Connection,
        snapshot_id: int | None,
        relationship_type: RelationshipType,
    ) -> frozenset[str]:
        if snapshot_id is None:
            return frozenset()
        rows = connection.execute(
            """
            SELECT a.username
            FROM snapshot_accounts AS sa
            JOIN accounts AS a ON a.id = sa.account_id
            WHERE sa.snapshot_id = ? AND sa.relationship_type = ?
            """,
            (snapshot_id, relationship_type.value),
        ).fetchall()
        return frozenset(str(row["username"]) for row in rows)

    @staticmethod
    def _upsert_account(
        connection: sqlite3.Connection, account: Account, stored_time: str
    ) -> int:
        username = normalize_username(account.username)
        if not username:
            raise ValueError("Encountered an empty username")
        connection.execute(
            """
            INSERT INTO accounts(
                instagram_user_id, username, profile_url, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                instagram_user_id = COALESCE(excluded.instagram_user_id, accounts.instagram_user_id),
                profile_url = excluded.profile_url,
                last_seen_at = excluded.last_seen_at
            """,
            (
                account.instagram_user_id,
                username,
                account.profile_url,
                stored_time,
                stored_time,
            ),
        )
        row = connection.execute(
            "SELECT id FROM accounts WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if row is None:
            raise DatabaseError(f"Could not resolve account id for @{username}")
        return int(row["id"])

    @staticmethod
    def _insert_relationships(
        connection: sqlite3.Connection,
        snapshot_id: int,
        usernames: Iterable[str],
        account_ids: Mapping[str, int],
        relationship_type: RelationshipType,
    ) -> None:
        rows = [
            (snapshot_id, account_ids[normalize_username(username)], relationship_type.value)
            for username in usernames
        ]
        connection.executemany(
            """
            INSERT INTO snapshot_accounts(snapshot_id, account_id, relationship_type)
            VALUES (?, ?, ?)
            """,
            rows,
        )

    @staticmethod
    def _insert_events(
        connection: sqlite3.Connection,
        snapshot_id: int,
        stored_time: str,
        changes: ChangeSet,
        account_ids: dict[str, int],
    ) -> None:
        event_groups = (
            (EventType.UNFOLLOWED_ME, changes.unfollowed_me),
            (EventType.FOLLOWED_ME, changes.new_followers),
            (EventType.I_UNFOLLOWED, changes.i_unfollowed),
            (EventType.I_FOLLOWED, changes.i_followed),
        )
        rows: list[tuple[int, str, int, str]] = []
        for event_type, usernames in event_groups:
            for username in usernames:
                account_id = account_ids.get(username)
                if account_id is None:
                    row = connection.execute(
                        "SELECT id FROM accounts WHERE username = ? COLLATE NOCASE",
                        (username,),
                    ).fetchone()
                    if row is None:
                        raise DatabaseError(f"Missing account for event: @{username}")
                    account_id = int(row["id"])
                rows.append((snapshot_id, stored_time, account_id, event_type.value))
        connection.executemany(
            """
            INSERT INTO events(snapshot_id, detected_at, account_id, event_type)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )

    def get_changes_for_snapshot(self, snapshot_id: int) -> ChangeSet:
        """Rebuild a ChangeSet from persisted events for one snapshot."""

        groups: dict[EventType, set[str]] = {event_type: set() for event_type in EventType}
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT e.event_type, a.username
                    FROM events AS e
                    JOIN accounts AS a ON a.id = e.account_id
                    WHERE e.snapshot_id = ?
                    ORDER BY a.username COLLATE NOCASE
                    """,
                    (snapshot_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load snapshot events: {exc}") from exc
        for row in rows:
            groups[EventType(str(row["event_type"]))].add(str(row["username"]))
        return ChangeSet(
            unfollowed_me=frozenset(groups[EventType.UNFOLLOWED_ME]),
            new_followers=frozenset(groups[EventType.FOLLOWED_ME]),
            i_unfollowed=frozenset(groups[EventType.I_UNFOLLOWED]),
            i_followed=frozenset(groups[EventType.I_FOLLOWED]),
        )

    def get_nonfollowers_details(self, snapshot_id: int) -> tuple[NonFollowerDetail, ...]:
        """Return current non-followers with historical mutual/unfollow context."""

        query = """
        SELECT
            a.username,
            a.profile_url,
            (
                SELECT MAX(s.created_at)
                FROM snapshots AS s
                WHERE EXISTS (
                    SELECT 1 FROM snapshot_accounts AS follower_state
                    WHERE follower_state.snapshot_id = s.id
                      AND follower_state.account_id = a.id
                      AND follower_state.relationship_type = 'follower'
                )
                AND EXISTS (
                    SELECT 1 FROM snapshot_accounts AS following_state
                    WHERE following_state.snapshot_id = s.id
                      AND following_state.account_id = a.id
                      AND following_state.relationship_type = 'following'
                )
            ) AS last_mutual_at,
            (
                SELECT MAX(e.detected_at)
                FROM events AS e
                WHERE e.account_id = a.id
                  AND e.event_type = 'UNFOLLOWED_ME'
            ) AS detected_unfollow_at
        FROM snapshot_accounts AS current_following
        JOIN accounts AS a ON a.id = current_following.account_id
        WHERE current_following.snapshot_id = ?
          AND current_following.relationship_type = 'following'
          AND NOT EXISTS (
              SELECT 1
              FROM snapshot_accounts AS current_follower
              WHERE current_follower.snapshot_id = current_following.snapshot_id
                AND current_follower.account_id = current_following.account_id
                AND current_follower.relationship_type = 'follower'
          )
        ORDER BY a.username COLLATE NOCASE
        """
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(query, (snapshot_id,)).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load non-followers: {exc}") from exc

        details: list[NonFollowerDetail] = []
        for row in rows:
            last_mutual = row["last_mutual_at"]
            detected_unfollow = row["detected_unfollow_at"]
            details.append(
                NonFollowerDetail(
                    username=str(row["username"]),
                    profile_url=str(row["profile_url"]),
                    status=(
                        NonFollowerStatus.UNFOLLOWED_YOU
                        if last_mutual is not None
                        else NonFollowerStatus.NEVER_FOLLOWED_BACK
                    ),
                    last_mutual_at=(
                        _from_storage_time(str(last_mutual)) if last_mutual is not None else None
                    ),
                    detected_unfollow_at=(
                        _from_storage_time(str(detected_unfollow))
                        if detected_unfollow is not None
                        else None
                    ),
                )
            )
        return tuple(details)

    def get_account_history(self, username: str) -> AccountHistory | None:
        """Return all snapshot states and explicit events for a username."""

        canonical_username = normalize_username(username)
        try:
            with closing(self._connect()) as connection:
                account = connection.execute(
                    """
                    SELECT id, username, profile_url, instagram_user_id
                    FROM accounts
                    WHERE username = ? COLLATE NOCASE
                    """,
                    (canonical_username,),
                ).fetchone()
                if account is None:
                    return None
                account_id = int(account["id"])
                state_rows = connection.execute(
                    """
                    SELECT
                        s.id,
                        s.created_at,
                        EXISTS (
                            SELECT 1 FROM snapshot_accounts AS sa
                            WHERE sa.snapshot_id = s.id
                              AND sa.account_id = ?
                              AND sa.relationship_type = 'follower'
                        ) AS is_follower,
                        EXISTS (
                            SELECT 1 FROM snapshot_accounts AS sa
                            WHERE sa.snapshot_id = s.id
                              AND sa.account_id = ?
                              AND sa.relationship_type = 'following'
                        ) AS is_following
                    FROM snapshots AS s
                    WHERE s.id >= (
                        SELECT MIN(snapshot_id)
                        FROM snapshot_accounts
                        WHERE account_id = ?
                    )
                    ORDER BY s.id
                    """,
                    (account_id, account_id, account_id),
                ).fetchall()
                event_rows = connection.execute(
                    """
                    SELECT snapshot_id, detected_at, event_type
                    FROM events
                    WHERE account_id = ?
                    ORDER BY snapshot_id, id
                    """,
                    (account_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DatabaseError(f"Could not load account history: {exc}") from exc

        states = tuple(
            AccountState(
                snapshot_id=int(row["id"]),
                created_at=_from_storage_time(str(row["created_at"])),
                is_follower=bool(row["is_follower"]),
                is_following=bool(row["is_following"]),
            )
            for row in state_rows
        )
        events = tuple(
            EventRecord(
                snapshot_id=int(row["snapshot_id"]),
                detected_at=_from_storage_time(str(row["detected_at"])),
                event_type=EventType(str(row["event_type"])),
            )
            for row in event_rows
        )
        return AccountHistory(
            username=str(account["username"]),
            profile_url=str(account["profile_url"]),
            instagram_user_id=(
                str(account["instagram_user_id"])
                if account["instagram_user_id"] is not None
                else None
            ),
            states=states,
            events=events,
        )
