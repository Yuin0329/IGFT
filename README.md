# Instagram Follow Tracker

Instagram Follow Tracker is a local command-line tool for recording changes in an Instagram account's follower relationships.

The tool uses Playwright to open the Instagram website in Chromium, collects the complete Followers and Following lists, and stores each successful scan in SQLite. Later scans are compared with the previous snapshot to identify new followers, unfollows, and other relationship changes.

It does not use an unofficial Instagram API, store account passwords, or run as a background service. All data remains on the local computer.

> For setup instructions in Traditional Chinese, see [中文操作說明](#中文操作說明).

## Features

- Stores every completed scan as a historical snapshot
- Tracks changes in both Followers and Following
- Lists mutual followers and accounts that do not follow back
- Records follow and unfollow events for individual accounts
- Distinguishes accounts that never followed back from accounts that were previously mutual
- Reuses a persistent Chromium profile for future scans
- Rejects suspiciously incomplete results before they are written to the database

## Requirements

- Windows 11
- Python 3.10 or later (Python 3.11+ recommended)
- Playwright Chromium
- An Instagram account that can be accessed through the desktop website

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

The examples in this README use the Python executable inside `.venv` directly, so activating the virtual environment is optional.

## Usage

### Manual login

Before the first scan, open the persistent browser profile and sign in to Instagram:

```powershell
.\.venv\Scripts\python.exe main.py login
```

Complete the login and any required security checks in the Chromium window. After confirming that the Instagram home page is available, close all Chromium windows opened by the command.

The command remains active until Chromium closes so the browser has time to save the session to `browser_data/`. It does not collect account lists or create a snapshot.

### Run a scan

```powershell
.\.venv\Scripts\python.exe main.py scan --username your_username
```

Enter the username without `@`. For example, if the profile URL is `https://www.instagram.com/example_user/`, run:

```powershell
.\.venv\Scripts\python.exe main.py scan --username example_user
```

Automatic profile detection is also available:

```powershell
.\.venv\Scripts\python.exe main.py scan
```

Using `--username` is recommended when Instagram's navigation layout prevents automatic detection.

Keep Chromium open until the scan finishes. A snapshot is saved only when both the Followers and Following lists have been collected and validated successfully.

### View results

Show the latest snapshot:

```powershell
.\.venv\Scripts\python.exe main.py status
```

Show changes detected by the latest scan:

```powershell
.\.venv\Scripts\python.exe main.py changes
```

List accounts you follow that do not follow you:

```powershell
.\.venv\Scripts\python.exe main.py nonfollowers
```

List mutual followers:

```powershell
.\.venv\Scripts\python.exe main.py mutual
```

Show the history of one account:

```powershell
.\.venv\Scripts\python.exe main.py history example_user
```

## Relationship Status

The `nonfollowers` command uses previous snapshots to provide additional context:

- `NEVER FOLLOWED BACK`: no saved snapshot contains a mutual-follow relationship with the account.
- `UNFOLLOWED YOU`: the account was mutual in an earlier snapshot but no longer follows you.

The first scan establishes the initial state. Change events are generated from the second successful scan onward.

## Incomplete Scan Protection

Instagram loads Followers and Following gradually. The scraper collects unique usernames while scrolling the list and stops only after several consecutive rounds produce no new accounts. A maximum number of rounds and timeouts prevent an infinite loop.

Before saving a new snapshot, Followers and Following are validated separately against the previous scan. If either list falls below 70% of its previous size, the result is considered incomplete and is not saved.

If a large decrease is genuine and the lists have been checked manually, the validation can be overridden explicitly:

```powershell
.\.venv\Scripts\python.exe main.py scan --username your_username --allow-large-drop
```

This option should only be used after confirming that the scan is complete.

## Local Data

The following runtime data is created automatically:

```text
data/tracker.db       SQLite snapshots and event history
browser_data/         Persistent Chromium profile and login session
logs/tracker.log      Diagnostic log
```

The `browser_data/` directory contains sensitive session information and should not be shared or committed to version control. Runtime data is excluded through `.gitignore`.

The application does not write passwords, cookies, or authentication tokens to SQLite or the log file.

## Testing

Run the offline test suite and syntax check with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q .
```

The database tests use temporary files and do not modify `data/tracker.db`.

## Notes

- CAPTCHA, 2FA, checkpoints, and other Instagram security prompts must be completed manually.
- The application does not attempt to bypass Instagram security systems.
- Only one `login` or `scan` process should use `browser_data/` at a time.
- Instagram may change its web interface without notice, so selectors may require future maintenance.
- This project is intended for personal use with your own account. Use it responsibly and follow Instagram's terms.

---

## 中文操作說明

以下說明涵蓋安裝、登入、掃描與結果查詢。所有指令皆於 VS Code 終端機中執行。

### 安裝

進入專案資料夾：

```powershell
cd "C:\path\to\ig-follow-tracker"
```

建立虛擬環境並安裝所需套件：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

上述安裝流程只需執行一次。

### 登入 Instagram

第一次使用前，先開啟專用的 Chromium 瀏覽器設定檔：

```powershell
.\.venv\Scripts\python.exe main.py login
```

在開啟的 Chromium 中手動登入 Instagram，並完成必要的安全驗證。確認 Instagram 首頁可以正常使用後，關閉此指令開啟的所有 Chromium 視窗。

Chromium 關閉前，終端機會維持執行狀態，這是正常行為。程式會在瀏覽器關閉後保留登入狀態，這個步驟不會執行掃描。

### 執行掃描

```powershell
.\.venv\Scripts\python.exe main.py scan --username 你的IG帳號
```

帳號名稱不需加上 `@`。例如，若個人頁網址為：

```text
https://www.instagram.com/example_user/
```

請執行：

```powershell
.\.venv\Scripts\python.exe main.py scan --username example_user
```

掃描期間請保持 Chromium 開啟。Followers 與 Following 都完成擷取並通過驗證後，程式才會將快照寫入資料庫。

第一次掃描只會建立比較基準。從第二次成功掃描開始，程式才會產生追蹤關係的變動紀錄。

### 查看未回追名單

```powershell
.\.venv\Scripts\python.exe main.py nonfollowers
```

名單中的狀態分為：

- `NEVER FOLLOWED BACK`：現有歷史紀錄中從未互追。
- `UNFOLLOWED YOU`：過去曾互追，但對方目前已取消追蹤。

### 查看最近變動

```powershell
.\.venv\Scripts\python.exe main.py changes
```

### 其他查詢指令

查看最新快照：

```powershell
.\.venv\Scripts\python.exe main.py status
```

查看互追名單：

```powershell
.\.venv\Scripts\python.exe main.py mutual
```

查看單一帳號的歷史：

```powershell
.\.venv\Scripts\python.exe main.py history example_user
```

### 後續使用

登入狀態尚未過期時，只需再次執行掃描：

```powershell
cd "C:\path\to\ig-follow-tracker"
.\.venv\Scripts\python.exe main.py scan --username 你的IG帳號
```

若登入狀態已過期，重新執行登入指令：

```powershell
.\.venv\Scripts\python.exe main.py login
```

同一時間請勿執行多個 `login` 或 `scan`。Chromium 尚未關閉時啟動另一個指令，可能會造成 `browser_data/` 被鎖定。
