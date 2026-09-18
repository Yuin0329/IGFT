"""Shared domain models for the tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RelationshipType(str, Enum):
    """Relationships stored for an account in a snapshot."""

    FOLLOWER = "follower"
    FOLLOWING = "following"


class EventType(str, Enum):
    """Changes detected between two consecutive snapshots."""

    FOLLOWED_ME = "FOLLOWED_ME"
    UNFOLLOWED_ME = "UNFOLLOWED_ME"
    I_FOLLOWED = "I_FOLLOWED"
    I_UNFOLLOWED = "I_UNFOLLOWED"


class NonFollowerStatus(str, Enum):
    """Historical classification for an account that does not follow back."""

    NEVER_FOLLOWED_BACK = "NEVER FOLLOWED BACK"
    UNFOLLOWED_YOU = "UNFOLLOWED YOU"


def normalize_username(username: str) -> str:
    """Return the canonical username representation used throughout the app."""

    return username.strip().removeprefix("@").strip().lower()


@dataclass(frozen=True, slots=True)
class Account:
    """A public account reference captured from an Instagram list."""

    username: str
    profile_url: str
    instagram_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Metadata for one complete scan."""

    id: int
    created_at: datetime
    followers_count: int
    following_count: int


@dataclass(frozen=True, slots=True)
class RelationshipAnalysis:
    """Set-based analysis of the two current relationship lists."""

    mutual: frozenset[str]
    not_following_back: frozenset[str]
    i_dont_follow_back: frozenset[str]


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """Differences between two consecutive snapshots."""

    unfollowed_me: frozenset[str] = field(default_factory=frozenset)
    new_followers: frozenset[str] = field(default_factory=frozenset)
    i_unfollowed: frozenset[str] = field(default_factory=frozenset)
    i_followed: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class StoredScan:
    """Result returned after an atomic snapshot commit."""

    current: Snapshot
    previous: Snapshot | None
    changes: ChangeSet


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A suspicious list count that should prevent a snapshot commit."""

    relationship_name: str
    previous_count: int
    current_count: int
    minimum_ratio: float


@dataclass(frozen=True, slots=True)
class NonFollowerDetail:
    """Historical context for an account that currently does not follow back."""

    username: str
    profile_url: str
    status: NonFollowerStatus
    last_mutual_at: datetime | None
    detected_unfollow_at: datetime | None


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One persisted relationship event."""

    snapshot_id: int
    detected_at: datetime
    event_type: EventType


@dataclass(frozen=True, slots=True)
class AccountState:
    """An account's relationship state at one snapshot."""

    snapshot_id: int
    created_at: datetime
    is_follower: bool
    is_following: bool


@dataclass(frozen=True, slots=True)
class AccountHistory:
    """Snapshot states and events associated with a known account."""

    username: str
    profile_url: str
    instagram_user_id: str | None
    states: tuple[AccountState, ...]
    events: tuple[EventRecord, ...]
