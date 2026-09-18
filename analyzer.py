"""Pure set-based relationship and scan validation logic."""

from __future__ import annotations

from collections.abc import Iterable

from models import ChangeSet, RelationshipAnalysis, ValidationIssue


def analyze_relationships(
    followers: Iterable[str], following: Iterable[str]
) -> RelationshipAnalysis:
    """Calculate the three useful views of the current relationship state."""

    follower_names = frozenset(followers)
    following_names = frozenset(following)
    return RelationshipAnalysis(
        mutual=follower_names & following_names,
        not_following_back=following_names - follower_names,
        i_dont_follow_back=follower_names - following_names,
    )


def compare_snapshots(
    old_followers: Iterable[str],
    old_following: Iterable[str],
    current_followers: Iterable[str],
    current_following: Iterable[str],
) -> ChangeSet:
    """Return changes between an old and current pair of complete lists."""

    old_follower_names = frozenset(old_followers)
    old_following_names = frozenset(old_following)
    current_follower_names = frozenset(current_followers)
    current_following_names = frozenset(current_following)

    return ChangeSet(
        unfollowed_me=old_follower_names - current_follower_names,
        new_followers=current_follower_names - old_follower_names,
        i_unfollowed=old_following_names - current_following_names,
        i_followed=current_following_names - old_following_names,
    )


def validate_scan_counts(
    previous_followers_count: int,
    previous_following_count: int,
    current_followers_count: int,
    current_following_count: int,
    minimum_ratio: float,
) -> tuple[ValidationIssue, ...]:
    """Flag list counts that dropped below the configured safety ratio."""

    issues: list[ValidationIssue] = []
    pairs = (
        ("followers", previous_followers_count, current_followers_count),
        ("following", previous_following_count, current_following_count),
    )
    for relationship_name, previous_count, current_count in pairs:
        if previous_count > 0 and current_count < previous_count * minimum_ratio:
            issues.append(
                ValidationIssue(
                    relationship_name=relationship_name,
                    previous_count=previous_count,
                    current_count=current_count,
                    minimum_ratio=minimum_ratio,
                )
            )
    return tuple(issues)

