# Instagram Follow Tracker

Instagram Follow Tracker is a local desktop application that records changes in an Instagram account's Followers and Following lists.

The application opens Instagram in Chromium through Playwright and stores each successful scan in SQLite. Later scans are compared with the previous snapshot to identify new followers, unfollows, and changes made to your Following list.

It does not use an unofficial Instagram API, store account passwords, or run in the background. Account data and browser sessions remain on the local computer.

> For setup and operating instructions in Traditional Chinese, see [中文操作說明](#中文操作說明).

## Features

- Desktop interface built with Tkinter
- Persistent Chromium profile for manual Instagram login
- Historical snapshots of Followers and Following
- New follower and unfollow event tracking
- Mutual and non-follower lists
- Per-account relationship history
- Incomplete-scan protection before data is saved
- Local SQLite storage

## Requirements

- Windows 11
- Python 3.10 or later (Python 3.11+ recommended)
- Playwright Chromium
- An Instagram account accessible through the desktop website

Visual Studio Code is recommended, but not required.

## Installation

Open a terminal in the project directory:

```powershell
cd "C:\path\to\ig-follow-tracker"
```

Create a virtual environment and install the required packages:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

The installation steps only need to be completed once.

## Starting the Application

```powershell
.\.venv\Scripts\python.exe main.py gui
```

## Interface Guide

### Account and scan controls

- **Instagram username**: Enter the username of the account currently signed in to Chromium. Do not include `@`.
- **Open Login Browser**: Opens the persistent Chromium profile. Use it for the first login, an expired session, 2FA, CAPTCHA, or another security check. Close all Chromium windows after login is complete.
- **Start Scan**: Collects the complete Followers and Following lists, validates the result, and saves a new snapshot.
- **Allow large decrease**: Allows a scan with a large drop in captured accounts to be saved. Use this only after manually confirming that the list is complete.

### Result buttons

- **Latest Status**: Shows the newest snapshot, list totals, and current relationship summary.
- **Recent Changes**: Shows differences between the two most recent snapshots.
- **Non-followers**: Lists accounts you follow that do not follow you back.
- **Mutual Followers**: Lists accounts that follow you and are also followed by you.
- **Account History**: Shows saved relationship states and events for one account. Replace the username field with the account you want to inspect before selecting this button. The account must have appeared in a saved Followers or Following list.
- **Clear Database**: Deletes all snapshots, events, and stored account history after a confirmation dialog. The Chromium login session is not removed. The next successful scan becomes a new initial snapshot.

## First Use

1. Start the application.
2. Select **Open Login Browser**.
3. Sign in to Instagram manually and complete any required security checks.
4. Confirm that Instagram opens normally, then close all Chromium windows.
5. Enter the signed-in account's username in the application.
6. Select **Start Scan**.

The first successful scan establishes the initial state and does not create change events. Changes are reported from the second successful scan onward.

## Relationship Status

The non-follower list uses saved snapshots to provide additional context:

- `NEVER FOLLOWED BACK`: no saved snapshot contains a mutual relationship with the account.
- `UNFOLLOWED YOU`: the account was mutual in an earlier snapshot but no longer follows you.

## Scan Safety

Instagram loads relationship lists gradually. The application collects unique profile links while scrolling and waits for the list to stop changing. If fewer accounts are captured than the count shown by Instagram, it performs another collection attempt. A result that still appears incomplete is not saved.

Followers and Following are also checked separately against the previous snapshot. By default, a list that falls below 70% of its previous size is rejected. The large-decrease option should only be enabled after the result has been checked manually.

Both lists must be collected and validated before a snapshot is committed. A failed scan does not create a partial snapshot.

## Local Data

The following runtime data is created automatically:

```text
data/tracker.db       SQLite snapshots and event history
browser_data/         Persistent Chromium profile and login session
logs/tracker.log      Diagnostic log
```

These files are excluded from Git through `.gitignore`. The `browser_data/` directory contains sensitive session information and should never be shared.

The application does not write passwords, cookies, or authentication tokens to SQLite or the log file.

## Testing

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q .
```

The database tests use temporary files and do not modify `data/tracker.db`.

## Notes

- CAPTCHA, 2FA, checkpoints, and other Instagram security prompts must be completed manually.
- The application does not attempt to bypass Instagram security systems.
- Do not open more than one login or scan process at the same time.
- Instagram may change its web interface without notice, so selectors may require future maintenance.
- This project is intended for personal use with your own account.

---

## 中文操作說明

### 安裝

在 VS Code 開啟專案資料夾後，於終端機執行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

以上步驟只需在第一次使用時執行。

### 啟動程式

```powershell
.\.venv\Scripts\python.exe main.py gui
```

### 帳號與掃描

- **Instagram username**：輸入目前登入的 Instagram 帳號名稱，不需加上 `@`。
- **開啟登入瀏覽器**：開啟程式專用的 Chromium。第一次使用、登入狀態過期或遇到安全驗證時使用。登入完成後，請關閉所有由程式開啟的 Chromium 視窗。
- **開始掃描**：抓取完整的 Followers 與 Following 名單，通過驗證後建立新的 Snapshot。
- **允許大幅下降**：當本次抓取數量明顯少於上次時，仍允許儲存結果。只有在確認名單確實完整時才建議勾選。

### 查詢功能

- **最新狀態**：顯示最新 Snapshot 的時間、Followers、Following 與目前關係統計。
- **最近變動**：比較最近兩次 Snapshot，顯示誰取消追蹤你、新增 Followers、你取消追蹤誰，以及你新追蹤誰。
- **未回追名單**：顯示你有追蹤、但對方沒有追蹤你的帳號。
- **互追名單**：顯示目前雙方互相追蹤的帳號。
- **帳號歷史**：查詢特定帳號過去的關係狀態與事件。請先將上方欄位改成要查詢的對方帳號，再按下此按鈕。該帳號必須曾出現在已儲存的 Followers 或 Following 名單中。自己的帳號通常不會出現在這兩份名單，因此不會有帳號歷史。
- **清空資料庫**：清除所有 Snapshot、變動事件與帳號歷史。按下後會先出現確認視窗，Instagram 登入狀態不會受到影響。清空後的第一次掃描會重新建立初始 Snapshot。

### 第一次使用

1. 啟動程式後，按下「開啟登入瀏覽器」。
2. 在 Chromium 中手動登入 Instagram，並完成必要的安全驗證。
3. 確認 Instagram 可以正常使用後，關閉所有 Chromium 視窗。
4. 回到程式，輸入目前登入的帳號名稱。
5. 按下「開始掃描」。

第一次成功掃描只會建立比較基準，不會將整份名單列為新增追蹤。從第二次成功掃描開始，程式才會顯示兩次 Snapshot 之間的變動。

### 後續使用

登入狀態仍有效時，開啟程式後可直接執行掃描。若 Instagram 要求重新登入、2FA、CAPTCHA 或其他安全驗證，請使用「開啟登入瀏覽器」手動完成。

掃描期間請保持 Chromium 開啟。Followers 與 Following 都完成擷取並通過驗證後，程式才會寫入 Snapshot；任何一份名單失敗都不會留下不完整紀錄。
