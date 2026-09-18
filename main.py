"""Command-line entry point for Instagram Follow Tracker."""

from __future__ import annotations

import argparse
import logging

import config
from analyzer import analyze_relationships, validate_scan_counts
from database import ConcurrentSnapshotError, DatabaseError, TrackerDatabase
from display import (
    print_account_history,
    print_header,
    print_mutual,
    print_nonfollowers,
    print_recent_changes,
    print_scan_result,
    print_status,
    print_validation_warning,
)
from logging_setup import configure_logging
from models import RelationshipType, Snapshot


LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the command parser and all supported subcommands."""

    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="Track Instagram follower/following snapshots using the web interface.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Run a complete foreground scan")
    scan_parser.add_argument(
        "--username",
        help=(
            "Explicit logged-in profile username. Use only if automatic profile "
            "navigation detection fails."
        ),
    )
    scan_parser.add_argument(
        "--allow-large-drop",
        action="store_true",
        help=(
            "Save even when a list drops below the configured safety ratio. "
            "Use only after visually confirming that the captured lists are complete."
        ),
    )

    subparsers.add_parser("status", help="Show the latest snapshot")
    subparsers.add_parser(
        "login", help="Open the persistent Chromium profile for manual login only"
    )
    subparsers.add_parser("changes", help="Show changes from the latest scan")
    subparsers.add_parser(
        "nonfollowers", help="Show accounts you follow that do not follow you"
    )
    subparsers.add_parser("mutual", help="Show the latest mutual-follow list")
    history_parser = subparsers.add_parser(
        "history", help="Show snapshot and event history for one account"
    )
    history_parser.add_argument("username", help="Instagram username, with or without @")
    return parser


def _load_latest_or_print(database: TrackerDatabase) -> Snapshot | None:
    latest = database.get_latest_snapshot()
    if latest is None:
        print_header()
        print("No snapshots exist yet. Run `python main.py scan` first.")
    return latest


def run_scan(args: argparse.Namespace, database: TrackerDatabase) -> int:
    """Run scraping, validate both list counts, then atomically commit."""

    try:
        from scraper import InstagramScraper, InstagramScraperError
    except ModuleNotFoundError as exc:
        if exc.name == "playwright":
            print(
                "ERROR: Playwright is not installed. Run `pip install -r requirements.txt` "
                "and `playwright install chromium`."
            )
            return 1
        raise

    previous = database.get_latest_snapshot()
    print("Opening a persistent Chromium window for Instagram...")
    try:
        followers, following = InstagramScraper(args.username).scan()
    except InstagramScraperError as exc:
        LOGGER.exception("Instagram scan aborted safely: %s", exc)
        print(f"\nERROR: {exc}")
        print("Snapshot was NOT saved.")
        return 1

    if previous is not None:
        issues = validate_scan_counts(
            previous_followers_count=previous.followers_count,
            previous_following_count=previous.following_count,
            current_followers_count=len(followers),
            current_following_count=len(following),
            minimum_ratio=config.SNAPSHOT_MIN_RATIO,
        )
        if issues and not args.allow_large_drop:
            LOGGER.warning("Suspicious scan counts rejected: %s", issues)
            print_validation_warning(issues)
            return 2
        if issues:
            LOGGER.warning(
                "Large count drop accepted by explicit --allow-large-drop override: %s",
                issues,
            )

    expected_previous_id = previous.id if previous is not None else None
    stored = database.commit_snapshot(
        followers=followers,
        following=following,
        expected_previous_id=expected_previous_id,
    )
    analysis = analyze_relationships(followers.keys(), following.keys())
    print_scan_result(stored, analysis)
    return 0


def run_status(database: TrackerDatabase) -> int:
    latest = _load_latest_or_print(database)
    if latest is None:
        return 1
    followers = database.get_relationship_usernames(latest.id, RelationshipType.FOLLOWER)
    following = database.get_relationship_usernames(latest.id, RelationshipType.FOLLOWING)
    print_status(latest, analyze_relationships(followers, following))
    return 0


def run_login() -> int:
    """Open the shared browser profile normally, without scan automation."""

    try:
        from scraper import InstagramScraperError, open_manual_login_browser
    except ModuleNotFoundError as exc:
        if exc.name == "playwright":
            print(
                "ERROR: Playwright is not installed. Run `pip install -r requirements.txt` "
                "and `playwright install chromium`."
            )
            return 1
        raise

    try:
        exit_code = open_manual_login_browser()
    except InstagramScraperError as exc:
        LOGGER.exception("Manual login browser failed: %s", exc)
        print(f"ERROR: {exc}")
        return 1
    if exit_code != 0:
        print(
            "Chromium exited with an error. Make sure no scan or other Chromium "
            "process is using browser_data, then try again."
        )
        return 1
    print("Manual-login browser closed. The persistent session is ready for a scan.")
    return 0


def run_changes(database: TrackerDatabase) -> int:
    latest = _load_latest_or_print(database)
    if latest is None:
        return 1
    previous = database.get_previous_snapshot(latest.id)
    changes = database.get_changes_for_snapshot(latest.id)
    print_recent_changes(latest, previous, changes)
    return 0


def run_nonfollowers(database: TrackerDatabase) -> int:
    latest = _load_latest_or_print(database)
    if latest is None:
        return 1
    print_nonfollowers(database.get_nonfollowers_details(latest.id), latest)
    return 0


def run_mutual(database: TrackerDatabase) -> int:
    latest = _load_latest_or_print(database)
    if latest is None:
        return 1
    followers = database.get_relationship_usernames(latest.id, RelationshipType.FOLLOWER)
    following = database.get_relationship_usernames(latest.id, RelationshipType.FOLLOWING)
    print_mutual(followers & following, latest)
    return 0


def run_history(username: str, database: TrackerDatabase) -> int:
    history = database.get_account_history(username)
    if history is None:
        print_header()
        print(f"No history found for {username!r}.")
        return 1
    print_account_history(history)
    return 0


def dispatch(args: argparse.Namespace, database: TrackerDatabase) -> int:
    """Dispatch a parsed subcommand."""

    if args.command == "scan":
        return run_scan(args, database)
    if args.command == "status":
        return run_status(database)
    if args.command == "login":
        return run_login()
    if args.command == "changes":
        return run_changes(database)
    if args.command == "nonfollowers":
        return run_nonfollowers(database)
    if args.command == "mutual":
        return run_mutual(database)
    if args.command == "history":
        return run_history(args.username, database)
    raise ValueError(f"Unknown command: {args.command}")


def main() -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    database = TrackerDatabase(config.DATABASE_PATH)
    try:
        database.initialize()
        return dispatch(args, database)
    except ConcurrentSnapshotError as exc:
        LOGGER.warning("Concurrent scan prevented: %s", exc)
        print(f"ERROR: {exc}")
        return 2
    except DatabaseError as exc:
        LOGGER.exception("Database operation failed: %s", exc)
        print(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        LOGGER.warning("Operation cancelled by user")
        print("\nCancelled. No incomplete snapshot was saved.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
