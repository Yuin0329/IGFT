"""Human-readable CLI rendering kept separate from application logic."""

from __future__ import annotations

from datetime import datetime

from models import (
    AccountHistory,
    ChangeSet,
    NonFollowerDetail,
    RelationshipAnalysis,
    Snapshot,
    StoredScan,
    ValidationIssue,
)


RULE = "=" * 41
SUBRULE = "-" * 41


def format_time(value: datetime | None) -> str:
    """Format a stored timestamp in the machine's local timezone."""

    if value is None:
        return "n/a"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def print_header() -> None:
    print(f"Instagram Follow Tracker\n{RULE}\n")


def _print_name_group(title: str, names: frozenset[str], marker: str) -> None:
    print(f"{title}: {len(names)}\n")
    for username in sorted(names, key=str.casefold):
        print(f"{marker} @{username}")
    print()


def print_relationship_summary(analysis: RelationshipAnalysis) -> None:
    print(f"Current relationship\n{SUBRULE}\n")
    print(f"Mutual:\n{len(analysis.mutual)}\n")
    print(f"Not following you back:\n{len(analysis.not_following_back)}\n")
    print(f"They follow you, you don't:\n{len(analysis.i_dont_follow_back)}")


def print_changes(changes: ChangeSet) -> None:
    print(f"Changes\n{SUBRULE}\n")
    _print_name_group("Unfollowed you", changes.unfollowed_me, "-")
    _print_name_group("New followers", changes.new_followers, "+")
    _print_name_group("You unfollowed", changes.i_unfollowed, "-")
    _print_name_group("You followed", changes.i_followed, "+")


def print_scan_result(result: StoredScan, analysis: RelationshipAnalysis) -> None:
    print_header()
    if result.previous is None:
        print("Initial snapshot created.\n")
        print(f"Snapshot #{result.current.id}")
        print(format_time(result.current.created_at))
        print(f"\nFollowers: {result.current.followers_count}")
        print(f"Following: {result.current.following_count}\n")
    else:
        print(f"Last scan:\n{format_time(result.previous.created_at)}\n")
        print(f"Current scan:\n{format_time(result.current.created_at)}\n")
        print(
            "Followers\n"
            f"{result.previous.followers_count} → {result.current.followers_count}\n"
        )
        print(
            "Following\n"
            f"{result.previous.following_count} → {result.current.following_count}\n"
        )
        print_changes(result.changes)
    print_relationship_summary(analysis)


def print_status(snapshot: Snapshot, analysis: RelationshipAnalysis) -> None:
    print_header()
    print(f"Latest snapshot: #{snapshot.id}")
    print(f"Scanned at: {format_time(snapshot.created_at)}\n")
    print(f"Followers: {snapshot.followers_count}")
    print(f"Following: {snapshot.following_count}\n")
    print_relationship_summary(analysis)


def print_recent_changes(
    current: Snapshot, previous: Snapshot | None, changes: ChangeSet
) -> None:
    print_header()
    if previous is None:
        print("The latest scan is the initial snapshot; there are no change events yet.")
        return
    print(f"Last scan:\n{format_time(previous.created_at)}\n")
    print(f"Current scan:\n{format_time(current.created_at)}\n")
    print(
        f"Followers: {previous.followers_count} → {current.followers_count}\n"
        f"Following: {previous.following_count} → {current.following_count}\n"
    )
    print_changes(changes)


def print_mutual(names: frozenset[str], snapshot: Snapshot) -> None:
    print_header()
    print(f"Mutual at snapshot #{snapshot.id}: {len(names)}\n{SUBRULE}")
    for username in sorted(names, key=str.casefold):
        print(f"@{username}")


def print_nonfollowers(
    details: tuple[NonFollowerDetail, ...], snapshot: Snapshot
) -> None:
    print_header()
    print(
        f"Not following you back at snapshot #{snapshot.id}: {len(details)}\n"
        f"{SUBRULE}\n"
    )
    if not details:
        print("Everyone you follow currently follows you back.")
        return
    for detail in details:
        print(f"@{detail.username}")
        print(f"status: {detail.status.value}")
        if detail.last_mutual_at is not None:
            print(f"last mutual: {format_time(detail.last_mutual_at)}")
        if detail.detected_unfollow_at is not None:
            print(f"detected unfollow: {format_time(detail.detected_unfollow_at)}")
        print()


def _state_label(is_follower: bool, is_following: bool) -> str:
    if is_follower and is_following:
        return "MUTUAL"
    if is_follower:
        return "FOLLOWS YOU; YOU DON'T FOLLOW"
    if is_following:
        return "YOU FOLLOW; DOESN'T FOLLOW YOU"
    return "NO CURRENT RELATIONSHIP"


def print_account_history(history: AccountHistory) -> None:
    print_header()
    print(f"History for @{history.username}")
    print(history.profile_url)
    if history.instagram_user_id:
        print(f"Instagram user ID: {history.instagram_user_id}")

    print(f"\nRelationship timeline\n{SUBRULE}")
    previous_state: tuple[bool, bool] | None = None
    for state in history.states:
        current_state = (state.is_follower, state.is_following)
        if current_state == previous_state:
            continue
        print(
            f"{format_time(state.created_at)}  Snapshot #{state.snapshot_id}  "
            f"{_state_label(*current_state)}"
        )
        previous_state = current_state

    print(f"\nEvents\n{SUBRULE}")
    if not history.events:
        print("No change events recorded (the account may only exist in the initial snapshot).")
        return
    for event in history.events:
        print(
            f"{format_time(event.detected_at)}  {event.event_type.value}  "
            f"(Snapshot #{event.snapshot_id})"
        )


def print_validation_warning(issues: tuple[ValidationIssue, ...]) -> None:
    print("\nWARNING:")
    print("The scan result looks incomplete.\n")
    for issue in issues:
        label = issue.relationship_name.capitalize()
        print(f"Previous {label.lower()}: {issue.previous_count}")
        print(f"Current captured {label.lower()}: {issue.current_count}")
        print(f"Minimum accepted ratio: {issue.minimum_ratio:.0%}\n")
    print("Snapshot was NOT saved.")

