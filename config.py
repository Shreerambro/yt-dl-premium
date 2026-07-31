"""
Configuration — loads everything from environment variables.
"""
import os
from pathlib import Path

# ─── Telegram ───────────────────────────────────────────────
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Comma-separated Telegram user IDs. Empty = public (anyone can use).
_auth = os.environ.get("AUTH_USERS", "")
AUTH_USERS: list[int] = [int(x) for x in _auth.split(",") if x.strip()]

# ─── Paths ──────────────────────────────────────────────────
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "./downloads"))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIES_DIR = Path(os.environ.get("COOKIES_DIR", "./cookies"))
COOKIES_DIR.mkdir(parents=True, exist_ok=True)

# ─── Limits ─────────────────────────────────────────────────
# Telegram MTProto limit = 2 GB ; Bot API limit = 50 MB
# Pyrogram uses MTProto → 2 GB cap
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024

# Seconds between progress-bar edits (avoid flood-wait)
PROGRESS_UPDATE_INTERVAL = 4
