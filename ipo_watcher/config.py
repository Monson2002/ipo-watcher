# ipo_watcher/config.py
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")  # all timestamps (logs, DB) are recorded in this timezone


def _env(name, default=""):
    # An unconfigured GitHub Actions secret expands to "", not an absent
    # variable - os.environ.get(name, default) would return "" instead of
    # default in that case, so empty is treated the same as unset here.
    return os.environ.get(name) or default


SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
ALERT_FROM = _env("ALERT_FROM", SMTP_USER)
ALERT_TO = _env("ALERT_TO")

IPO_GURU_API_KEY = _env("IPO_GURU_API_KEY")

DB_PATH = _env("IPO_WATCHER_DB_PATH", "data/ipo_watcher.db")
PDF_DIR = _env("IPO_WATCHER_PDF_DIR", "data/pdfs")
LOG_PATH = _env("IPO_WATCHER_LOG_PATH", "logs/ipo_watcher.log")
