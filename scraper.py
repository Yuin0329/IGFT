"""Playwright-based Instagram web scraper using a persistent browser profile."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from time import monotonic
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

import config
from models import Account, normalize_username


LOGGER = logging.getLogger(__name__)


class InstagramScraperError(RuntimeError):
    """Base exception for safe, user-facing scraping failures."""


class InstagramDOMError(InstagramScraperError):
    """Raised when the expected Instagram web structure is unavailable."""


class InstagramAuthenticationError(InstagramScraperError):
    """Raised when the user has not completed login."""


class InstagramSecurityCheckError(InstagramScraperError):
    """Raised when Instagram requires a manual security action."""


class InstagramIncompleteListError(InstagramScraperError):
    """Raised instead of returning a list that may be incomplete."""


@dataclass(frozen=True, slots=True)
class InstagramSelectors:
    """Central catalog of selectors used against Instagram's changeable DOM."""

    all_links: str = "a[href]"
    login_links: str = 'a[href*="/accounts/login"]'
    login_username_input: str = 'input[name="username"]'
    login_form: str = 'form[action*="/accounts/login"]'
    dialogs: str = '[role="dialog"]'
    modal_profile_links: str = "a[href]"
    scroll_candidates: str = "div"
    security_surfaces: str = (
        'iframe[src*="captcha"], form[action*="challenge"], '
        'input[name="verificationCode"]'
    )


SELECTORS = InstagramSelectors()

RESERVED_PROFILE_PATHS = frozenset(
    {
        "about",
        "accounts",
        "api",
        "challenge",
        "developer",
        "direct",
        "directory",
        "emails",
        "explore",
        "graphql",
        "legal",
        "oauth",
        "p",
        "press",
        "privacy",
        "reel",
        "reels",
        "stories",
        "terms",
        "web",
    }
)
USERNAME_PATTERN = re.compile(r"^[a-z0-9._]{1,30}$", re.IGNORECASE)

PROFILE_LABELS = frozenset(
    {
        "profile",
        "your profile",
        "個人檔案",
        "个人主页",
        "個人主頁",
        "프로필",
        "プロフィール",
        "profil",
        "perfil",
    }
)

EMPTY_LIST_PATTERNS = (
    "no followers yet",
    "not following anyone",
    "尚無粉絲",
    "還沒有粉絲",
    "尚未追蹤任何人",
    "还没有粉丝",
    "尚未关注任何人",
)

RELATIONSHIP_TEXT_PATTERNS = {
    "followers": re.compile(
        r"\bfollowers?\b|粉絲|追蹤者|关注者|팔로워|フォロワー", re.IGNORECASE
    ),
    "following": re.compile(
        r"\bfollowing\b|追蹤中|正在关注|已關注|已关注|팔로잉|フォロー中",
        re.IGNORECASE,
    ),
}

SCROLL_TO_END_SCRIPT = """
root => {
    const nodes = [root, ...root.querySelectorAll('__SCROLL_CANDIDATES__')];
    const candidates = nodes.filter((node) => {
        const style = window.getComputedStyle(node);
        const overflow = style.overflowY;
        return (overflow === 'auto' || overflow === 'scroll') &&
               node.scrollHeight > node.clientHeight;
    });
    const target = candidates.sort(
        (a, b) => (b.scrollHeight - b.clientHeight) -
                  (a.scrollHeight - a.clientHeight)
    )[0] || root;
    const before = target.scrollTop;
    target.scrollTop = target.scrollHeight;
    target.dispatchEvent(new Event('scroll', {bubbles: true}));
    return {
        before: before,
        after: target.scrollTop,
        scrollHeight: target.scrollHeight,
        clientHeight: target.clientHeight,
        usedScrollableDescendant: candidates.length > 0
    };
}
""".replace("__SCROLL_CANDIDATES__", SELECTORS.scroll_candidates)


def extract_username_from_profile_url(url: str) -> str | None:
    """Extract a normalized username from a direct Instagram profile URL.

    Only one-segment paths on instagram.com are accepted. Routes such as
    /explore/, /accounts/, /reels/, /direct/, posts, and stories are rejected.
    """

    if not url or url.startswith(("javascript:", "mailto:")):
        return None
    absolute_url = urljoin(config.INSTAGRAM_BASE_URL, url)
    parsed = urlparse(absolute_url)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"instagram.com", "www.instagram.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        return None
    username = normalize_username(parts[0])
    if username in RESERVED_PROFILE_PATHS or not USERNAME_PATTERN.fullmatch(username):
        return None
    return username


def canonical_profile_url(username: str) -> str:
    """Build the canonical public web profile URL for a normalized username."""

    return f"{config.INSTAGRAM_BASE_URL}{normalize_username(username)}/"


def open_manual_login_browser() -> int:
    """Open the persistent profile without Playwright browser automation.

    This is a manual-login fallback for accounts whose authentication flow does
    not render correctly while the browser is under Playwright control.
    """

    config.BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        executable_path = playwright.chromium.executable_path

    print(
        "Opening Chromium in manual-login mode.\n"
        "Log in to Instagram normally, confirm the home page opens, then close "
        "all windows from this Chromium profile.\n"
        "The password is handled only by Instagram and is not read by this program."
    )
    try:
        completed = subprocess.run(
            [
                executable_path,
                f"--user-data-dir={config.BROWSER_DATA_DIR}",
                "--profile-directory=Default",
                "--no-first-run",
                config.INSTAGRAM_BASE_URL,
            ],
            check=False,
        )
    except OSError as exc:
        raise InstagramScraperError(
            "Could not open Chromium for manual login."
        ) from exc
    return completed.returncode


class InstagramScraper:
    """Run one foreground scan in a visible persistent Chromium context."""

    def __init__(self, requested_username: str | None = None) -> None:
        self.requested_username = (
            normalize_username(requested_username) if requested_username else None
        )
        if self.requested_username and not USERNAME_PATTERN.fullmatch(
            self.requested_username
        ):
            raise InstagramScraperError(
                f"Invalid Instagram username: {requested_username!r}"
            )

    def scan(self) -> tuple[dict[str, Account], dict[str, Account]]:
        """Capture complete followers and following lists or raise an error."""

        config.BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Opening Instagram")
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(config.BROWSER_DATA_DIR),
                    headless=config.HEADLESS,
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.set_default_timeout(config.DOM_TIMEOUT_MS)
                    page.set_default_navigation_timeout(config.NAVIGATION_TIMEOUT_MS)
                    self._open_instagram(page)
                    username = self.requested_username or self._find_logged_in_username(page)
                    self._open_profile(page, username)

                    followers = self._capture_relationship(page, username, "followers")
                    LOGGER.info("Followers loaded: %s", len(followers))
                    following = self._capture_relationship(page, username, "following")
                    LOGGER.info("Following loaded: %s", len(following))
                    return followers, following
                finally:
                    context.close()
        except InstagramScraperError:
            raise
        except PlaywrightError as exc:
            raise InstagramScraperError(
                "Playwright could not complete the browser operation. Check the network, "
                "close any other Chromium process using browser_data, and review the log."
            ) from exc

    def _open_instagram(self, page: Page) -> None:
        try:
            page.goto(config.INSTAGRAM_BASE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(config.PAGE_SETTLE_MS)
        except PlaywrightTimeoutError as exc:
            raise InstagramScraperError(
                "Instagram did not finish loading within the navigation timeout."
            ) from exc

        if self._security_check_is_visible(page):
            self._pause_for_security_check()
        if self._login_is_visible(page):
            self._wait_for_manual_login(page)

    def _wait_for_manual_login(self, page: Page) -> None:
        """Keep the browser open while the user completes login interactively."""

        print(
            "\nPlease log in to Instagram in the opened browser.\n\n"
            "After login is complete, press ENTER here."
        )
        try:
            input()
        except EOFError as exc:
            raise InstagramAuthenticationError(
                "Login requires an interactive terminal."
            ) from exc
        page.wait_for_timeout(config.PAGE_SETTLE_MS)
        if self._security_check_is_visible(page):
            raise InstagramSecurityCheckError(
                "Instagram still shows a CAPTCHA, 2FA, checkpoint, or challenge. "
                "Complete it manually, then run the scan again."
            )
        if self._login_is_visible(page):
            raise InstagramAuthenticationError(
                "Instagram still shows the login page. No snapshot was saved."
            )

    def _pause_for_security_check(self) -> None:
        print(
            "\nInstagram requires a CAPTCHA, 2FA, checkpoint, or security challenge.\n"
            "Complete it manually in the opened browser, then press ENTER here.\n"
            "This scan will stop safely; run it again after the check is cleared."
        )
        try:
            input()
        except EOFError:
            pass
        raise InstagramSecurityCheckError(
            "Instagram required a manual security check. No snapshot was saved."
        )

    @staticmethod
    def _login_is_visible(page: Page) -> bool:
        path = urlparse(page.url).path.lower()
        if "/accounts/login" in path:
            return True
        for selector in (
            SELECTORS.login_username_input,
            SELECTORS.login_form,
            SELECTORS.login_links,
        ):
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                return True
        return False

    @staticmethod
    def _security_check_is_visible(page: Page) -> bool:
        path = urlparse(page.url).path.lower()
        if any(
            marker in path
            for marker in ("/challenge", "/checkpoint", "/auth_platform/recaptcha")
        ):
            return True
        locator = page.locator(SELECTORS.security_surfaces).first
        return bool(locator.count() and locator.is_visible())

    def _find_logged_in_username(self, page: Page) -> str:
        """Find the profile navigation link without reading cookies or tokens."""

        candidates = page.locator(SELECTORS.all_links).evaluate_all(
            """
            links => links.map((link, index) => ({
                index,
                href: link.getAttribute('href') || '',
                aria: (link.getAttribute('aria-label') || '').trim().toLowerCase(),
                text: (link.textContent || '').trim().toLowerCase(),
                childAria: (link.querySelector('[aria-label]')?.getAttribute('aria-label') || '')
                    .trim().toLowerCase(),
                imageAlt: (link.querySelector('img[alt]')?.getAttribute('alt') || '')
                    .trim().toLowerCase(),
                insideNav: Boolean(link.closest('nav'))
            }))
            """
        )
        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            username = extract_username_from_profile_url(str(candidate["href"]))
            if username is None:
                continue
            aria = str(candidate["aria"])
            text = str(candidate["text"])
            child_aria = str(candidate["childAria"])
            image_alt = str(candidate["imageAlt"])
            score = 0
            if aria in PROFILE_LABELS or child_aria in PROFILE_LABELS:
                score = 100
            elif text in PROFILE_LABELS:
                score = 90
            elif bool(candidate["insideNav"]):
                score = 70
            elif "profile picture" in image_alt and username in image_alt:
                score = 30
            if score:
                scored.append((score, username))

        if not scored:
            raise InstagramDOMError(
                "Could not identify the logged-in account's profile link. Instagram's "
                "navigation DOM may have changed. Re-run with `scan --username YOUR_NAME` "
                "to select the profile explicitly."
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        username = scored[0][1]
        LOGGER.info("Logged-in profile identified as @%s", username)
        return username

    def _open_profile(self, page: Page, username: str) -> None:
        if self._security_check_is_visible(page):
            self._pause_for_security_check()
        profile_url = canonical_profile_url(username)
        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            page.wait_for_timeout(config.PAGE_SETTLE_MS)
        except PlaywrightTimeoutError as exc:
            raise InstagramScraperError(
                f"Profile page for @{username} did not load in time."
            ) from exc
        if self._security_check_is_visible(page):
            self._pause_for_security_check()
        if self._login_is_visible(page):
            self._wait_for_manual_login(page)
            try:
                page.goto(profile_url, wait_until="domcontentloaded")
                page.wait_for_timeout(config.PAGE_SETTLE_MS)
            except PlaywrightTimeoutError as exc:
                raise InstagramScraperError(
                    f"Profile page for @{username} did not load after login."
                ) from exc
            if self._security_check_is_visible(page):
                self._pause_for_security_check()
            if self._login_is_visible(page):
                raise InstagramAuthenticationError(
                    "Instagram returned to login after the manual login step. "
                    "No snapshot was saved."
                )
        self._dismiss_non_list_dialogs(page)

    @staticmethod
    def _dismiss_non_list_dialogs(page: Page) -> None:
        """Dismiss transient notification prompts before opening a list modal."""

        dialogs = page.locator(SELECTORS.dialogs)
        for _ in range(2):
            visible = any(dialogs.nth(i).is_visible() for i in range(dialogs.count()))
            if not visible:
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

    def _capture_relationship(
        self, page: Page, username: str, relationship_path: str
    ) -> dict[str, Account]:
        if relationship_path not in {"followers", "following"}:
            raise ValueError(f"Unsupported relationship: {relationship_path}")
        dialog = self._open_relationship_modal(page, username, relationship_path)
        try:
            accounts = self._scroll_and_collect(page, dialog, relationship_path)
        finally:
            self._close_modal(page, dialog)
        return accounts

    def _open_relationship_modal(
        self, page: Page, username: str, relationship_path: str
    ) -> Locator:
        expected_path = f"/{username}/{relationship_path}/".lower()
        links = page.locator(SELECTORS.all_links)
        target: Locator | None = None
        for index in range(links.count()):
            link = links.nth(index)
            href = link.get_attribute("href") or ""
            parsed_path = urlparse(urljoin(config.INSTAGRAM_BASE_URL, href)).path.lower()
            if parsed_path == expected_path and link.is_visible():
                target = link
                break
        if target is None:
            text_pattern = RELATIONSHIP_TEXT_PATTERNS[relationship_path]
            for index in range(links.count()):
                link = links.nth(index)
                if not link.is_visible():
                    continue
                href = (link.get_attribute("href") or "").strip()
                link_text = link.inner_text().strip()
                if href in {"", "#"} and text_pattern.search(link_text):
                    target = link
                    LOGGER.info(
                        "Using semantic text fallback for the %s profile control",
                        relationship_path,
                    )
                    break
        if target is None:
            raise InstagramDOMError(
                f"Could not find the {relationship_path} control on @{username}'s profile. "
                "No snapshot was saved."
            )

        try:
            target.scroll_into_view_if_needed()
            target.click()
            dialog = self._wait_for_visible_dialog(page)
        except PlaywrightTimeoutError as exc:
            raise InstagramDOMError(
                f"The {relationship_path} modal did not open. Instagram's DOM may have changed."
            ) from exc
        return dialog

    @staticmethod
    def _wait_for_visible_dialog(page: Page) -> Locator:
        deadline = monotonic() + config.DOM_TIMEOUT_MS / 1000
        while monotonic() < deadline:
            dialogs = page.locator(SELECTORS.dialogs)
            for index in range(dialogs.count() - 1, -1, -1):
                dialog = dialogs.nth(index)
                if dialog.is_visible():
                    return dialog
            page.wait_for_timeout(200)
        raise PlaywrightTimeoutError("No visible relationship dialog appeared")

    def _scroll_and_collect(
        self, page: Page, dialog: Locator, relationship_path: str
    ) -> dict[str, Account]:
        accounts = self._wait_for_initial_list_state(page, dialog, relationship_path)
        seen_usernames: set[str] = set(accounts)
        no_change_rounds = 0

        for round_number in range(1, config.MAX_SCROLL_ROUNDS + 1):
            self._assert_dialog_open(dialog, relationship_path)
            before_count = len(seen_usernames)
            try:
                metrics = dialog.evaluate(SCROLL_TO_END_SCRIPT)
            except PlaywrightTimeoutError as exc:
                raise InstagramDOMError(
                    f"The {relationship_path} modal became unavailable while scrolling."
                ) from exc

            page.wait_for_timeout(int(config.SCROLL_DELAY_SECONDS * 1000))
            self._assert_dialog_open(dialog, relationship_path)
            visible_accounts = self._extract_visible_accounts(dialog)
            accounts.update(visible_accounts)
            seen_usernames.update(visible_accounts)
            current_count = len(seen_usernames)

            if current_count == before_count:
                no_change_rounds += 1
            else:
                no_change_rounds = 0
                LOGGER.info(
                    "%s loaded: %s (scroll round %s)",
                    relationship_path.capitalize(),
                    current_count,
                    round_number,
                )

            LOGGER.debug(
                "%s scroll metrics: unique=%s no_change=%s metrics=%s",
                relationship_path,
                current_count,
                no_change_rounds,
                metrics,
            )
            if no_change_rounds >= config.MAX_NO_CHANGE_ROUNDS:
                return accounts

        raise InstagramIncompleteListError(
            f"The {relationship_path} list was still changing after "
            f"{config.MAX_SCROLL_ROUNDS} scroll rounds. It was not saved."
        )

    def _wait_for_initial_list_state(
        self, page: Page, dialog: Locator, relationship_path: str
    ) -> dict[str, Account]:
        deadline = monotonic() + config.INITIAL_LIST_TIMEOUT_MS / 1000
        while monotonic() < deadline:
            self._assert_dialog_open(dialog, relationship_path)
            accounts = self._extract_visible_accounts(dialog)
            if accounts:
                return accounts
            text = dialog.inner_text().strip().lower()
            if any(pattern in text for pattern in EMPTY_LIST_PATTERNS):
                return {}
            page.wait_for_timeout(500)
        raise InstagramDOMError(
            f"The {relationship_path} modal opened but no account rows or recognized "
            "empty-list message appeared. Returning an empty list would be unsafe, so "
            "no snapshot was saved."
        )

    @staticmethod
    def _extract_visible_accounts(dialog: Locator) -> dict[str, Account]:
        hrefs = dialog.locator(SELECTORS.modal_profile_links).evaluate_all(
            "links => links.map(link => link.getAttribute('href') || '')"
        )
        accounts: dict[str, Account] = {}
        for href in hrefs:
            username = extract_username_from_profile_url(str(href))
            if username is None:
                continue
            accounts[username] = Account(
                username=username,
                profile_url=canonical_profile_url(username),
                instagram_user_id=None,
            )
        return accounts

    @staticmethod
    def _assert_dialog_open(dialog: Locator, relationship_path: str) -> None:
        try:
            if dialog.count() != 1 or not dialog.is_visible():
                raise InstagramDOMError(
                    f"The {relationship_path} modal closed unexpectedly. No snapshot was saved."
                )
        except PlaywrightTimeoutError as exc:
            raise InstagramDOMError(
                f"Could not inspect the {relationship_path} modal. No snapshot was saved."
            ) from exc

    @staticmethod
    def _close_modal(page: Page, dialog: Locator) -> None:
        if not dialog.count() or not dialog.is_visible():
            return
        page.keyboard.press("Escape")
        try:
            dialog.wait_for(state="hidden", timeout=config.MODAL_CLOSE_TIMEOUT_MS)
        except PlaywrightTimeoutError as exc:
            raise InstagramDOMError(
                "Could not close the Instagram relationship modal cleanly."
            ) from exc
