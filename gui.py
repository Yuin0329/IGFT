"""Tkinter desktop interface for Instagram Follow Tracker."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable, Iterable
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk
from typing import Literal

import config
from analyzer import analyze_relationships, validate_scan_counts
from database import DatabaseError, TrackerDatabase
from logging_setup import configure_logging
from models import (
    AccountHistory,
    ChangeSet,
    EventType,
    NonFollowerDetail,
    NonFollowerStatus,
    RelationshipAnalysis,
    RelationshipType,
    Snapshot,
    StoredScan,
    ValidationIssue,
    normalize_username,
)


LOGGER = logging.getLogger(__name__)

LanguageCode = Literal["en", "zh_TW"]
LANGUAGE_NAMES: dict[LanguageCode, str] = {
    "en": "English",
    "zh_TW": "繁體中文",
}
LANGUAGE_CODES = {name: code for code, name in LANGUAGE_NAMES.items()}


class GuiTaskError(RuntimeError):
    """An expected task failure that can be shown directly in the GUI."""


def _pick(language: LanguageCode, english: str, chinese: str) -> str:
    """Return text in the selected interface language."""

    return chinese if language == "zh_TW" else english


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _name_lines(names: Iterable[str], marker: str = "") -> str:
    prefix = f"{marker} " if marker else ""
    return "\n".join(
        f"{prefix}@{username}" for username in sorted(names, key=str.casefold)
    )


def _format_relationship_summary(
    analysis: RelationshipAnalysis, language: LanguageCode = "en"
) -> str:
    if language == "zh_TW":
        return (
            "目前關係\n"
            "────────────────────────\n"
            f"互追：{len(analysis.mutual)}\n"
            f"對方未回追：{len(analysis.not_following_back)}\n"
            f"對方追蹤你，但你未回追：{len(analysis.i_dont_follow_back)}"
        )
    return (
        "Current relationships\n"
        "────────────────────────\n"
        f"Mutual: {len(analysis.mutual)}\n"
        f"Not following you back: {len(analysis.not_following_back)}\n"
        f"They follow you, you don't: {len(analysis.i_dont_follow_back)}"
    )


def _format_change_set(changes: ChangeSet, language: LanguageCode = "en") -> str:
    if language == "zh_TW":
        groups = (
            ("取消追蹤你", changes.unfollowed_me, "-"),
            ("新的 Followers", changes.new_followers, "+"),
            ("你取消追蹤", changes.i_unfollowed, "-"),
            ("你新追蹤", changes.i_followed, "+"),
        )
        separator = "："
    else:
        groups = (
            ("Unfollowed you", changes.unfollowed_me, "-"),
            ("New followers", changes.new_followers, "+"),
            ("You unfollowed", changes.i_unfollowed, "-"),
            ("You followed", changes.i_followed, "+"),
        )
        separator = ": "

    sections: list[str] = []
    for title, names, marker in groups:
        lines = _name_lines(names, marker)
        sections.append(
            f"{title}{separator}{len(names)}" + (f"\n{lines}" if lines else "")
        )
    return "\n\n".join(sections)


def _format_scan_result(
    result: StoredScan,
    analysis: RelationshipAnalysis,
    language: LanguageCode = "en",
) -> str:
    current = result.current
    if language == "zh_TW":
        if result.previous is None:
            heading = (
                f"已建立第一個快照 #{current.id}\n"
                f"時間：{_format_time(current.created_at)}\n"
                f"Followers：{current.followers_count}\n"
                f"Following：{current.following_count}"
            )
        else:
            previous = result.previous
            heading = (
                f"掃描完成，已儲存快照 #{current.id}\n"
                f"上次：{_format_time(previous.created_at)}\n"
                f"本次：{_format_time(current.created_at)}\n\n"
                f"Followers：{previous.followers_count} → {current.followers_count}\n"
                f"Following：{previous.following_count} → {current.following_count}\n\n"
                "最近變動\n────────────────────────\n"
                f"{_format_change_set(result.changes, language)}"
            )
    elif result.previous is None:
        heading = (
            f"Initial snapshot #{current.id} created\n"
            f"Time: {_format_time(current.created_at)}\n"
            f"Followers: {current.followers_count}\n"
            f"Following: {current.following_count}"
        )
    else:
        previous = result.previous
        heading = (
            f"Scan completed. Snapshot #{current.id} saved\n"
            f"Previous: {_format_time(previous.created_at)}\n"
            f"Current: {_format_time(current.created_at)}\n\n"
            f"Followers: {previous.followers_count} → {current.followers_count}\n"
            f"Following: {previous.following_count} → {current.following_count}\n\n"
            "Recent changes\n────────────────────────\n"
            f"{_format_change_set(result.changes, language)}"
        )
    return f"{heading}\n\n{_format_relationship_summary(analysis, language)}"


def _format_validation_issues(
    issues: tuple[ValidationIssue, ...], language: LanguageCode = "en"
) -> str:
    if language == "zh_TW":
        lines = ["掃描結果可能不完整，因此未建立快照。", ""]
        for issue in issues:
            lines.extend(
                (
                    issue.relationship_name.capitalize(),
                    f"上次數量：{issue.previous_count}",
                    f"本次擷取：{issue.current_count}",
                    f"最低比例：{issue.minimum_ratio:.0%}",
                    "",
                )
            )
        lines.append("請重新掃描，或在確認結果完整後勾選「允許大幅下降」。")
        return "\n".join(lines)

    lines = ["The scan result may be incomplete. No snapshot was saved.", ""]
    for issue in issues:
        lines.extend(
            (
                issue.relationship_name.capitalize(),
                f"Previous count: {issue.previous_count}",
                f"Current captured: {issue.current_count}",
                f"Minimum ratio: {issue.minimum_ratio:.0%}",
                "",
            )
        )
    lines.append(
        "Run the scan again, or enable Allow large decrease only after confirming "
        "that both lists are complete."
    )
    return "\n".join(lines)


def _state_label(
    is_follower: bool, is_following: bool, language: LanguageCode = "en"
) -> str:
    if language == "zh_TW":
        if is_follower and is_following:
            return "互追"
        if is_follower:
            return "對方追蹤你；你未追蹤對方"
        if is_following:
            return "你追蹤對方；對方未回追"
        return "目前沒有追蹤關係"
    if is_follower and is_following:
        return "Mutual"
    if is_follower:
        return "Follows you; you don't follow them"
    if is_following:
        return "You follow them; they don't follow you"
    return "No current relationship"


def _nonfollower_status_label(
    status: NonFollowerStatus, language: LanguageCode
) -> str:
    if language == "zh_TW":
        return {
            NonFollowerStatus.NEVER_FOLLOWED_BACK: "從未回追",
            NonFollowerStatus.UNFOLLOWED_YOU: "曾經互追 → 已取消追蹤你",
        }[status]
    return {
        NonFollowerStatus.NEVER_FOLLOWED_BACK: "Never followed back",
        NonFollowerStatus.UNFOLLOWED_YOU: "Previously mutual → unfollowed you",
    }[status]


def _event_label(event_type: EventType, language: LanguageCode) -> str:
    if language == "zh_TW":
        return {
            EventType.FOLLOWED_ME: "開始追蹤你",
            EventType.UNFOLLOWED_ME: "取消追蹤你",
            EventType.I_FOLLOWED: "你開始追蹤",
            EventType.I_UNFOLLOWED: "你取消追蹤",
        }[event_type]
    return {
        EventType.FOLLOWED_ME: "Followed you",
        EventType.UNFOLLOWED_ME: "Unfollowed you",
        EventType.I_FOLLOWED: "You followed",
        EventType.I_UNFOLLOWED: "You unfollowed",
    }[event_type]


class TrackerGUI:
    """Small desktop control panel backed by the existing tracker modules."""

    def __init__(self, root: tk.Tk, database: TrackerDatabase) -> None:
        self.root = root
        self.database = database
        self.language: LanguageCode = "en"
        self.language_choice = tk.StringVar(value=LANGUAGE_NAMES["en"])
        self.username = tk.StringVar()
        self.allow_large_drop = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Ready")
        self._busy = False
        self._buttons: list[ttk.Button] = []
        self._localized_buttons: list[tuple[ttk.Button, str, str]] = []
        self._results: queue.Queue[tuple[bool, str]] = queue.Queue()

        self._configure_window()
        self._build_layout()
        self._apply_language(reset_output=True)
        self.root.after(100, self._process_results)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _t(self, english: str, chinese: str) -> str:
        return _pick(self.language, english, chinese)

    def _configure_window(self) -> None:
        self.root.title("Instagram Follow Tracker")
        self.root.geometry("980x700")
        self.root.minsize(800, 560)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky=tk.EW)
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Instagram Follow Tracker",
            font=("Segoe UI", 17, "bold"),
        ).grid(row=0, column=0, sticky=tk.W)
        self.language_label = ttk.Label(header)
        self.language_label.grid(row=0, column=1, padx=(12, 6))
        self.language_combo = ttk.Combobox(
            header,
            textvariable=self.language_choice,
            values=tuple(LANGUAGE_NAMES.values()),
            state="readonly",
            width=12,
        )
        self.language_combo.grid(row=0, column=2, sticky=tk.E)
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        self.controls_frame = ttk.LabelFrame(container, padding=12)
        self.controls_frame.grid(row=1, column=0, sticky=tk.EW, pady=(12, 8))
        self.controls_frame.columnconfigure(1, weight=1)

        self.username_label = ttk.Label(self.controls_frame)
        self.username_label.grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        username_entry = ttk.Entry(self.controls_frame, textvariable=self.username)
        username_entry.grid(row=0, column=1, sticky=tk.EW)
        username_entry.focus_set()

        login_button = ttk.Button(self.controls_frame, command=self._start_login)
        login_button.grid(row=0, column=2, padx=(10, 6))
        scan_button = ttk.Button(self.controls_frame, command=self._start_scan)
        scan_button.grid(row=0, column=3)
        self._register_button(
            login_button, "Open Login Browser", "開啟登入瀏覽器"
        )
        self._register_button(scan_button, "Start Scan", "開始掃描")

        self.allow_drop_check = ttk.Checkbutton(
            self.controls_frame,
            variable=self.allow_large_drop,
        )
        self.allow_drop_check.grid(
            row=1, column=1, columnspan=3, sticky=tk.W, pady=(8, 0)
        )

        actions = ttk.Frame(container)
        actions.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
        action_specs = (
            ("Latest Status", "最新狀態", self._show_status),
            ("Recent Changes", "最近變動", self._show_changes),
            ("Non-followers", "未回追名單", self._show_nonfollowers),
            ("Mutual", "互追名單", self._show_mutual),
            ("Account History", "帳號歷史", self._show_history),
            ("Clear Database", "清空資料庫", self._clear_database),
        )
        for column, (english, chinese, command) in enumerate(action_specs):
            button = ttk.Button(actions, command=command)
            button.grid(row=0, column=column, padx=(0, 8))
            self._register_button(button, english, chinese)

        self.output_frame = ttk.LabelFrame(container, padding=8)
        self.output_frame.grid(row=3, column=0, sticky=tk.NSEW)
        self.output_frame.columnconfigure(0, weight=1)
        self.output_frame.rowconfigure(0, weight=1)

        self.output = scrolledtext.ScrolledText(
            self.output_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            padx=10,
            pady=10,
            state=tk.DISABLED,
        )
        self.output.grid(row=0, column=0, sticky=tk.NSEW)

        status_bar = ttk.Label(container, textvariable=self.status_text, anchor=tk.W)
        status_bar.grid(row=4, column=0, sticky=tk.EW, pady=(8, 0))

    def _register_button(
        self, button: ttk.Button, english: str, chinese: str
    ) -> None:
        self._buttons.append(button)
        self._localized_buttons.append((button, english, chinese))

    def _on_language_changed(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        selected = self.language_choice.get()
        self.language = LANGUAGE_CODES.get(selected, "en")
        self._apply_language(reset_output=True)

    def _apply_language(self, reset_output: bool) -> None:
        self.language_label.configure(text=self._t("Language", "語言"))
        self.controls_frame.configure(text=self._t("Account and Scan", "帳號與掃描"))
        self.username_label.configure(text="Instagram username")
        self.allow_drop_check.configure(
            text=self._t(
                "Allow large decrease (use only after confirming complete lists)",
                "允許大幅下降（僅在確認名單完整時使用）",
            )
        )
        self.output_frame.configure(text=self._t("Results", "結果"))
        for button, english, chinese in self._localized_buttons:
            button.configure(text=self._t(english, chinese))
        self.status_text.set(self._t("Ready", "就緒"))
        if reset_output:
            self._set_output(
                self._t(
                    "Enter the Instagram username first.\n\n"
                    "On first use, select Open Login Browser. Sign in, close all "
                    "Chromium windows, then select Start Scan.",
                    "請先輸入 Instagram username。\n\n"
                    "第一次使用請按「開啟登入瀏覽器」，登入完成並關閉所有 Chromium "
                    "視窗後，再按「開始掃描」。",
                )
            )

    def _set_output(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, text.strip() + "\n")
        self.output.configure(state=tk.DISABLED)
        self.output.see("1.0")

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._buttons:
            button.configure(state=state)
        self.language_combo.configure(state="disabled" if busy else "readonly")
        self.status_text.set(status or self._t("Ready", "就緒"))
        self.root.configure(cursor="watch" if busy else "")

    def _run_task(self, status: str, task: Callable[[], str]) -> None:
        if self._busy:
            messagebox.showinfo(
                self._t("In progress", "執行中"),
                self._t(
                    "Wait for the current task to finish.",
                    "請等待目前的工作完成。",
                ),
                parent=self.root,
            )
            return
        self._set_busy(True, status)
        self._set_output(f"{status}\n\n{self._t('Please wait…', '請稍候……')}")

        def worker() -> None:
            try:
                self._results.put((True, task()))
            except (GuiTaskError, DatabaseError) as exc:
                LOGGER.warning("GUI task failed: %s", exc)
                self._results.put((False, str(exc)))
            except Exception as exc:  # Keep unexpected worker failures out of Tk.
                LOGGER.exception("Unexpected GUI task failure")
                self._results.put(
                    (
                        False,
                        self._t(
                            f"An unexpected error occurred: {exc}",
                            f"發生未預期的錯誤：{exc}",
                        ),
                    )
                )

        threading.Thread(target=worker, daemon=True).start()

    def _process_results(self) -> None:
        try:
            succeeded, text = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._set_busy(
                False,
                self._t("Complete", "完成")
                if succeeded
                else self._t("Not completed", "未完成"),
            )
            self._set_output(text)
            if not succeeded:
                messagebox.showerror(
                    self._t("Operation not completed", "操作未完成"),
                    text,
                    parent=self.root,
                )
        finally:
            self.root.after(100, self._process_results)

    def _start_login(self) -> None:
        def task() -> str:
            from scraper import InstagramScraperError, open_manual_login_browser

            try:
                exit_code = open_manual_login_browser()
            except InstagramScraperError as exc:
                raise GuiTaskError(str(exc)) from exc
            if exit_code != 0:
                raise GuiTaskError(
                    self._t(
                        "Chromium did not close normally. Make sure no other process "
                        "is using browser_data.",
                        "Chromium 未正常關閉。請確認沒有其他程式正在使用 browser_data。",
                    )
                )
            return self._t(
                "The login browser is closed and the session has been saved.\n\n"
                "Confirm the username, then select Start Scan.",
                "登入瀏覽器已關閉，登入狀態已保存。\n\n"
                "確認 username 後即可按「開始掃描」。",
            )

        self._run_task(
            self._t(
                "Waiting for manual login; close all Chromium windows when finished",
                "等待手動登入；完成後請關閉所有 Chromium 視窗",
            ),
            task,
        )

    def _start_scan(self) -> None:
        requested_username = normalize_username(self.username.get())
        allow_large_drop = self.allow_large_drop.get()

        def task() -> str:
            from scraper import InstagramScraper, InstagramScraperError

            previous = self.database.get_latest_snapshot()
            try:
                followers, following = InstagramScraper(
                    requested_username or None,
                    interactive_login=False,
                ).scan()
            except InstagramScraperError as exc:
                raise GuiTaskError(str(exc)) from exc

            if previous is not None:
                issues = validate_scan_counts(
                    previous_followers_count=previous.followers_count,
                    previous_following_count=previous.following_count,
                    current_followers_count=len(followers),
                    current_following_count=len(following),
                    minimum_ratio=config.SNAPSHOT_MIN_RATIO,
                )
                if issues and not allow_large_drop:
                    raise GuiTaskError(_format_validation_issues(issues, self.language))
                if issues:
                    LOGGER.warning("GUI accepted large count drop: %s", issues)

            stored = self.database.commit_snapshot(
                followers=followers,
                following=following,
                expected_previous_id=previous.id if previous is not None else None,
            )
            analysis = analyze_relationships(followers, following)
            return _format_scan_result(stored, analysis, self.language)

        self._run_task(
            self._t(
                "Collecting Followers and Following",
                "正在擷取 Followers 與 Following",
            ),
            task,
        )

    def _latest_snapshot(self) -> Snapshot:
        latest = self.database.get_latest_snapshot()
        if latest is None:
            raise GuiTaskError(
                self._t(
                    "No snapshots exist yet. Complete a scan first.",
                    "目前沒有快照。請先完成一次掃描。",
                )
            )
        return latest

    def _show_status(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            followers = self.database.get_relationship_usernames(
                latest.id, RelationshipType.FOLLOWER
            )
            following = self.database.get_relationship_usernames(
                latest.id, RelationshipType.FOLLOWING
            )
            analysis = analyze_relationships(followers, following)
            if self.language == "zh_TW":
                heading = (
                    f"最新快照 #{latest.id}\n"
                    f"時間：{_format_time(latest.created_at)}\n"
                    f"Followers：{latest.followers_count}\n"
                    f"Following：{latest.following_count}"
                )
            else:
                heading = (
                    f"Latest snapshot #{latest.id}\n"
                    f"Time: {_format_time(latest.created_at)}\n"
                    f"Followers: {latest.followers_count}\n"
                    f"Following: {latest.following_count}"
                )
            return f"{heading}\n\n{_format_relationship_summary(analysis, self.language)}"

        self._run_task(
            self._t("Loading latest status", "正在讀取最新狀態"), task
        )

    def _show_changes(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            previous = self.database.get_previous_snapshot(latest.id)
            if previous is None:
                return self._t(
                    "The latest scan is the initial snapshot; there are no changes "
                    "to compare yet.",
                    "目前只有第一個快照，尚無可比較的變動。",
                )
            title = self._t("Recent changes", "最近變動")
            changes = self.database.get_changes_for_snapshot(latest.id)
            return (
                f"{title}\n"
                f"{_format_time(previous.created_at)} → "
                f"{_format_time(latest.created_at)}\n\n"
                f"{_format_change_set(changes, self.language)}"
            )

        self._run_task(
            self._t("Loading recent changes", "正在讀取最近變動"), task
        )

    def _show_nonfollowers(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            details = self.database.get_nonfollowers_details(latest.id)
            return self._format_nonfollowers(details, latest)

        self._run_task(
            self._t("Loading non-followers", "正在讀取未回追名單"), task
        )

    def _format_nonfollowers(
        self, details: tuple[NonFollowerDetail, ...], latest: Snapshot
    ) -> str:
        if self.language == "zh_TW":
            lines = [f"未回追名單（快照 #{latest.id}）：{len(details)}", ""]
            empty_message = "目前追蹤的帳號都有回追。"
        else:
            lines = [
                f"Not following you back (snapshot #{latest.id}): {len(details)}",
                "",
            ]
            empty_message = "Everyone you follow currently follows you back."
        if not details:
            lines.append(empty_message)
            return "\n".join(lines)
        for detail in details:
            lines.append(f"@{detail.username}")
            lines.append(
                self._t("Status: ", "狀態：")
                + _nonfollower_status_label(detail.status, self.language)
            )
            if detail.last_mutual_at is not None:
                lines.append(
                    self._t("Last mutual: ", "最後互追：")
                    + _format_time(detail.last_mutual_at)
                )
            if detail.detected_unfollow_at is not None:
                lines.append(
                    self._t("Detected unfollow: ", "偵測取消追蹤：")
                    + _format_time(detail.detected_unfollow_at)
                )
            lines.append("")
        return "\n".join(lines)

    def _show_mutual(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            followers = self.database.get_relationship_usernames(
                latest.id, RelationshipType.FOLLOWER
            )
            following = self.database.get_relationship_usernames(
                latest.id, RelationshipType.FOLLOWING
            )
            mutual = followers & following
            names = _name_lines(mutual)
            title = self._t(
                f"Mutual accounts (snapshot #{latest.id}): {len(mutual)}",
                f"互追名單（快照 #{latest.id}）：{len(mutual)}",
            )
            empty = self._t(
                "There are no mutual accounts.", "目前沒有互追帳號。"
            )
            return title + (f"\n\n{names}" if names else f"\n\n{empty}")

        self._run_task(
            self._t("Loading mutual accounts", "正在讀取互追名單"), task
        )

    def _show_history(self) -> None:
        username = normalize_username(self.username.get())
        if not username:
            messagebox.showinfo(
                self._t("Username required", "需要帳號"),
                self._t(
                    "Enter the account to look up in the username field first.",
                    "請先在 username 欄位輸入要查詢的帳號。",
                ),
                parent=self.root,
            )
            return

        def task() -> str:
            history = self.database.get_account_history(username)
            if history is None:
                raise GuiTaskError(
                    self._t(
                        f"No history was found for @{username}.",
                        f"找不到 @{username} 的歷史紀錄。",
                    )
                )
            return self._format_history(history)

        self._run_task(
            self._t(
                f"Loading history for @{username}",
                f"正在讀取 @{username} 的歷史",
            ),
            task,
        )

    def _clear_database(self) -> None:
        """Clear all saved tracker data after an explicit confirmation."""

        if self._busy:
            messagebox.showinfo(
                self._t("Busy", "目前忙碌中"),
                self._t(
                    "Wait for the current task to finish and try again.",
                    "請等待目前的操作完成後再試一次。",
                ),
                parent=self.root,
            )
            return
        confirmed = messagebox.askyesno(
            self._t("Confirm database reset", "確認清空資料庫"),
            self._t(
                "This permanently deletes every snapshot, change event, and account "
                "history.\n\nThe Instagram login session will be preserved. The next "
                "scan will create a new initial snapshot.\n\nContinue?",
                "這會永久刪除所有 Snapshot、變動事件與帳號歷史。\n\n"
                "Instagram 登入狀態不會被刪除。清空後，下一次掃描會建立新的初始 "
                "Snapshot。\n\n確定要繼續嗎？",
            ),
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            snapshot_count, account_count = self.database.clear_all_data()
        except DatabaseError as exc:
            LOGGER.exception("Could not clear tracker database")
            messagebox.showerror(
                self._t("Reset failed", "清空失敗"), str(exc), parent=self.root
            )
            self.status_text.set(self._t("Reset failed", "清空失敗"))
            return

        result = self._t(
            "The database has been cleared.\n\n"
            f"Snapshots deleted: {snapshot_count}\n"
            f"Accounts deleted: {account_count}\n\n"
            "The login session is still available. Run a scan to create a new "
            "initial snapshot.",
            "資料庫已清空。\n\n"
            f"已刪除 Snapshot：{snapshot_count}\n"
            f"已刪除帳號資料：{account_count}\n\n"
            "登入狀態仍保留。請執行一次掃描來建立新的初始 Snapshot。",
        )
        self._set_output(result)
        self.status_text.set(self._t("Database cleared", "資料庫已清空"))
        messagebox.showinfo(
            self._t("Reset complete", "清空完成"), result, parent=self.root
        )

    def _format_history(self, history: AccountHistory) -> str:
        lines = [
            self._t(
                f"History for @{history.username}",
                f"@{history.username} 的歷史",
            ),
            history.profile_url,
            "",
            self._t("Relationship timeline", "關係變化"),
        ]
        previous_state: tuple[bool, bool] | None = None
        for state in history.states:
            current_state = (state.is_follower, state.is_following)
            if current_state == previous_state:
                continue
            lines.append(
                f"{_format_time(state.created_at)}  #{state.snapshot_id}  "
                f"{_state_label(state.is_follower, state.is_following, self.language)}"
            )
            previous_state = current_state

        lines.extend(("", self._t("Events", "事件")))
        if not history.events:
            lines.append(self._t("No change events recorded.", "尚無變動事件。"))
        else:
            for event in history.events:
                lines.append(
                    f"{_format_time(event.detected_at)}  "
                    f"{_event_label(event.event_type, self.language)}  "
                    f"#{event.snapshot_id}"
                )
        return "\n".join(lines)

    def _on_close(self) -> None:
        if self._busy and not messagebox.askyesno(
            self._t("Task still running", "工作仍在執行"),
            self._t(
                "A background task is still running. Close the window anyway?",
                "目前仍有工作在背景執行。確定要關閉視窗嗎？",
            ),
            parent=self.root,
        ):
            return
        self.root.destroy()


def launch_gui(database: TrackerDatabase | None = None) -> None:
    """Initialize persistence and start the Tk event loop."""

    configure_logging()
    active_database = database or TrackerDatabase(config.DATABASE_PATH)
    active_database.initialize()
    root = tk.Tk()
    TrackerGUI(root, active_database)
    root.mainloop()


if __name__ == "__main__":
    launch_gui()
