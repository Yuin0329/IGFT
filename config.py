"""Application configuration and filesystem locations."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
APPLICATION_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else PROJECT_ROOT
)
DATA_DIR = APPLICATION_ROOT / "data"
DATABASE_PATH = DATA_DIR / "tracker.db"
BROWSER_DATA_DIR = APPLICATION_ROOT / "browser_data"
LOG_DIR = APPLICATION_ROOT / "logs"
LOG_FILE = LOG_DIR / "tracker.log"

INSTAGRAM_BASE_URL = "https://www.instagram.com/"

# The visible browser is intentional: login, 2FA, CAPTCHA, and security checks
# must always be completed by the user.
HEADLESS = False

SCROLL_DELAY_SECONDS = 1.5
MAX_NO_CHANGE_ROUNDS = 4
MAX_SCROLL_ROUNDS = 500
MAX_LIST_CAPTURE_ATTEMPTS = 2

NAVIGATION_TIMEOUT_MS = 45_000
DOM_TIMEOUT_MS = 15_000
INITIAL_LIST_TIMEOUT_MS = 20_000
MODAL_CLOSE_TIMEOUT_MS = 5_000
PAGE_SETTLE_MS = 1_500
PROFILE_AVAILABILITY_TIMEOUT_MS = 15_000
PROFILE_AVAILABILITY_SETTLE_MS = 750

# A list that suddenly loses more than 30% of its rows is rejected by default.
SNAPSHOT_MIN_RATIO = 0.70

LOG_LEVEL = logging.INFO
LOG_MAX_BYTES = 2_000_000
LOG_BACKUP_COUNT = 3
