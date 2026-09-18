"""Tkinter desktop interface for Instagram Follow Tracker."""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable, Iterable
from datetime import datetime
from tkinter import messagebox, scrolledtext, ttk

import config
from analyzer import analyze_relationships, validate_scan_counts
from database import DatabaseError, TrackerDatabase
from logging_setup import configure_logging
from models import (
    AccountHistory,
    ChangeSet,
    NonFollowerDetail,
    RelationshipAnalysis,
    RelationshipType,
    Snapshot,
    StoredScan,
    ValidationIssue,
    normalize_username,
)


LOGGER = logging.getLogger(__name__)


class GuiTaskError(RuntimeError):
    """An expected task failure that can be shown directly in the GUI."""


def _format_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _name_lines(names: Iterable[str], marker: str = "") -> str:
    prefix = f"{marker} " if marker else ""
    return "\n".join(
        f"{prefix}@{username}" for username in sorted(names, key=str.casefold)
    )


def _format_relationship_summary(analysis: RelationshipAnalysis) -> str:
    return (
        "目前關係\n"
        "────────────────────────\n"
        f"互追：{len(analysis.mutual)}\n"
        f"對方未回追：{len(analysis.not_following_back)}\n"
        f"對方追蹤你，但你未回追：{len(analysis.i_dont_follow_back)}"
    )


def _format_change_set(changes: ChangeSet) -> str:
    groups = (
        ("取消追蹤你", changes.unfollowed_me, "-"),
        ("新的 Followers", changes.new_followers, "+"),
        ("你取消追蹤", changes.i_unfollowed, "-"),
        ("你新追蹤", changes.i_followed, "+"),
    )
    sections: list[str] = []
    for title, names, marker in groups:
        lines = _name_lines(names, marker)
        sections.append(f"{title}：{len(names)}" + (f"\n{lines}" if lines else ""))
    return "\n\n".join(sections)


def _format_scan_result(result: StoredScan, analysis: RelationshipAnalysis) -> str:
    current = result.current
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
            f"最近變動\n────────────────────────\n"
            f"{_format_change_set(result.changes)}"
        )
    return f"{heading}\n\n{_format_relationship_summary(analysis)}"


def _format_validation_issues(issues: tuple[ValidationIssue, ...]) -> str:
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


def _state_label(is_follower: bool, is_following: bool) -> str:
    if is_follower and is_following:
        return "互追"
    if is_follower:
        return "對方追蹤你；你未追蹤對方"
    if is_following:
        return "你追蹤對方；對方未回追"
    return "目前沒有追蹤關係"


class TrackerGUI:
    """Small desktop control panel backed by the existing tracker modules."""

    def __init__(self, root: tk.Tk, database: TrackerDatabase) -> None:
        self.root = root
        self.database = database
        self.username = tk.StringVar()
        self.allow_large_drop = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="就緒")
        self._busy = False
        self._buttons: list[ttk.Button] = []
        self._results: queue.Queue[tuple[bool, str]] = queue.Queue()

        self._configure_window()
        self._build_layout()
        self.root.after(100, self._process_results)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_window(self) -> None:
        self.root.title("Instagram Follow Tracker")
        self.root.geometry("920x680")
        self.root.minsize(760, 540)
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        title = ttk.Label(
            container,
            text="Instagram Follow Tracker",
            font=("Segoe UI", 17, "bold"),
        )
        title.grid(row=0, column=0, sticky=tk.W)

        controls = ttk.LabelFrame(container, text="帳號與掃描", padding=12)
        controls.grid(row=1, column=0, sticky=tk.EW, pady=(12, 8))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Instagram username").grid(
            row=0, column=0, sticky=tk.W, padx=(0, 8)
        )
        username_entry = ttk.Entry(controls, textvariable=self.username)
        username_entry.grid(row=0, column=1, sticky=tk.EW)
        username_entry.focus_set()

        login_button = ttk.Button(
            controls, text="開啟登入瀏覽器", command=self._start_login
        )
        login_button.grid(row=0, column=2, padx=(10, 6))
        scan_button = ttk.Button(controls, text="開始掃描", command=self._start_scan)
        scan_button.grid(row=0, column=3)
        self._buttons.extend((login_button, scan_button))

        ttk.Checkbutton(
            controls,
            text="允許大幅下降（僅在確認名單完整時使用）",
            variable=self.allow_large_drop,
        ).grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))

        actions = ttk.Frame(container)
        actions.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
        action_specs = (
            ("最新狀態", self._show_status),
            ("最近變動", self._show_changes),
            ("未回追名單", self._show_nonfollowers),
            ("互追名單", self._show_mutual),
            ("帳號歷史", self._show_history),
            ("清空資料庫", self._clear_database),
        )
        for column, (label, command) in enumerate(action_specs):
            button = ttk.Button(actions, text=label, command=command)
            button.grid(row=0, column=column, padx=(0, 8))
            self._buttons.append(button)

        output_frame = ttk.LabelFrame(container, text="結果", padding=8)
        output_frame.grid(row=3, column=0, sticky=tk.NSEW)
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)

        self.output = scrolledtext.ScrolledText(
            output_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            padx=10,
            pady=10,
            state=tk.DISABLED,
        )
        self.output.grid(row=0, column=0, sticky=tk.NSEW)
        self._set_output(
            "請先輸入 Instagram username。\n\n"
            "第一次使用請按「開啟登入瀏覽器」，登入完成並關閉 Chromium 後，"
            "再按「開始掃描」。"
        )

        status_bar = ttk.Label(container, textvariable=self.status_text, anchor=tk.W)
        status_bar.grid(row=4, column=0, sticky=tk.EW, pady=(8, 0))

    def _set_output(self, text: str) -> None:
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, text.strip() + "\n")
        self.output.configure(state=tk.DISABLED)
        self.output.see("1.0")

    def _set_busy(self, busy: bool, status: str = "就緒") -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._buttons:
            button.configure(state=state)
        self.status_text.set(status)
        self.root.configure(cursor="watch" if busy else "")

    def _run_task(self, status: str, task: Callable[[], str]) -> None:
        if self._busy:
            messagebox.showinfo("執行中", "請等待目前的工作完成。")
            return
        self._set_busy(True, status)
        self._set_output(f"{status}\n\n請稍候……")

        def worker() -> None:
            try:
                self._results.put((True, task()))
            except (GuiTaskError, DatabaseError) as exc:
                LOGGER.warning("GUI task failed: %s", exc)
                self._results.put((False, str(exc)))
            except Exception as exc:  # Keep unexpected worker failures out of Tk.
                LOGGER.exception("Unexpected GUI task failure")
                self._results.put((False, f"發生未預期的錯誤：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _process_results(self) -> None:
        try:
            succeeded, text = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._set_busy(False, "完成" if succeeded else "未完成")
            self._set_output(text)
            if not succeeded:
                messagebox.showerror("操作未完成", text)
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
                    "Chromium 未正常關閉。請確認沒有其他程式正在使用 browser_data。"
                )
            return (
                "登入瀏覽器已關閉，登入狀態已保存。\n\n"
                "確認 username 後即可按「開始掃描」。"
            )

        self._run_task("等待手動登入；完成後請關閉所有 Chromium 視窗", task)

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
                    raise GuiTaskError(_format_validation_issues(issues))
                if issues:
                    LOGGER.warning("GUI accepted large count drop: %s", issues)

            stored = self.database.commit_snapshot(
                followers=followers,
                following=following,
                expected_previous_id=previous.id if previous is not None else None,
            )
            analysis = analyze_relationships(followers, following)
            return _format_scan_result(stored, analysis)

        self._run_task("正在擷取 Followers 與 Following", task)

    def _latest_snapshot(self) -> Snapshot:
        latest = self.database.get_latest_snapshot()
        if latest is None:
            raise GuiTaskError("目前沒有快照。請先完成一次掃描。")
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
            return (
                f"最新快照 #{latest.id}\n"
                f"時間：{_format_time(latest.created_at)}\n"
                f"Followers：{latest.followers_count}\n"
                f"Following：{latest.following_count}\n\n"
                f"{_format_relationship_summary(analysis)}"
            )

        self._run_task("正在讀取最新狀態", task)

    def _show_changes(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            previous = self.database.get_previous_snapshot(latest.id)
            if previous is None:
                return "目前只有第一個快照，尚無可比較的變動。"
            changes = self.database.get_changes_for_snapshot(latest.id)
            return (
                f"最近變動\n"
                f"{_format_time(previous.created_at)} → {_format_time(latest.created_at)}\n\n"
                f"{_format_change_set(changes)}"
            )

        self._run_task("正在讀取最近變動", task)

    def _show_nonfollowers(self) -> None:
        def task() -> str:
            latest = self._latest_snapshot()
            details = self.database.get_nonfollowers_details(latest.id)
            return self._format_nonfollowers(details, latest)

        self._run_task("正在讀取未回追名單", task)

    @staticmethod
    def _format_nonfollowers(
        details: tuple[NonFollowerDetail, ...], latest: Snapshot
    ) -> str:
        lines = [f"未回追名單（快照 #{latest.id}）：{len(details)}", ""]
        if not details:
            lines.append("目前追蹤的帳號都有回追。")
            return "\n".join(lines)
        for detail in details:
            lines.append(f"@{detail.username}")
            lines.append(f"狀態：{detail.status.value}")
            if detail.last_mutual_at is not None:
                lines.append(f"最後互追：{_format_time(detail.last_mutual_at)}")
            if detail.detected_unfollow_at is not None:
                lines.append(
                    f"偵測取消追蹤：{_format_time(detail.detected_unfollow_at)}"
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
            return f"互追名單（快照 #{latest.id}）：{len(mutual)}" + (
                f"\n\n{names}" if names else "\n\n目前沒有互追帳號。"
            )

        self._run_task("正在讀取互追名單", task)

    def _show_history(self) -> None:
        username = normalize_username(self.username.get())
        if not username:
            messagebox.showinfo("需要帳號", "請先在 username 欄位輸入要查詢的帳號。")
            return

        def task() -> str:
            history = self.database.get_account_history(username)
            if history is None:
                raise GuiTaskError(f"找不到 @{username} 的歷史紀錄。")
            return self._format_history(history)

        self._run_task(f"正在讀取 @{username} 的歷史", task)

    def _clear_database(self) -> None:
        """Clear all saved tracker data after an explicit confirmation."""

        if self._busy:
            messagebox.showinfo("目前忙碌中", "請等待目前的操作完成後再試一次。")
            return
        confirmed = messagebox.askyesno(
            "確認清空資料庫",
            "這會永久刪除所有 Snapshot、變動事件與帳號歷史。\n\n"
            "Instagram 登入狀態不會被刪除。清空後，下一次掃描會建立新的初始 Snapshot。\n\n"
            "確定要繼續嗎？",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        try:
            snapshot_count, account_count = self.database.clear_all_data()
        except DatabaseError as exc:
            LOGGER.exception("Could not clear tracker database")
            messagebox.showerror("清空失敗", str(exc), parent=self.root)
            self.status_text.set("清空失敗")
            return

        result = (
            "資料庫已清空。\n\n"
            f"已刪除 Snapshot：{snapshot_count}\n"
            f"已刪除帳號資料：{account_count}\n\n"
            "登入狀態仍保留。請執行一次掃描來建立新的初始 Snapshot。"
        )
        self._set_output(result)
        self.status_text.set("資料庫已清空")
        messagebox.showinfo("清空完成", result, parent=self.root)

    @staticmethod
    def _format_history(history: AccountHistory) -> str:
        lines = [f"@{history.username} 的歷史", history.profile_url, "", "關係變化"]
        previous_state: tuple[bool, bool] | None = None
        for state in history.states:
            current_state = (state.is_follower, state.is_following)
            if current_state == previous_state:
                continue
            lines.append(
                f"{_format_time(state.created_at)}　#{state.snapshot_id}　"
                f"{_state_label(*current_state)}"
            )
            previous_state = current_state

        lines.extend(("", "事件"))
        if not history.events:
            lines.append("尚無變動事件。")
        else:
            for event in history.events:
                lines.append(
                    f"{_format_time(event.detected_at)}　{event.event_type.value}　"
                    f"#{event.snapshot_id}"
                )
        return "\n".join(lines)

    def _on_close(self) -> None:
        if self._busy and not messagebox.askyesno(
            "工作仍在執行",
            "目前仍有工作在背景執行。確定要關閉視窗嗎？",
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
