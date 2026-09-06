# ipo_watcher/config.py
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

IST = ZoneInfo("Asia/Kolkata")  # all timestamps (logs, DB) are recorded in this timezone

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_FROM = os.environ.get("ALERT_FROM", SMTP_USER)
ALERT_TO = os.environ.get("ALERT_TO", "")

DB_PATH = os.environ.get("IPO_WATCHER_DB_PATH", "data/ipo_watcher.db")
PDF_DIR = os.environ.get("IPO_WATCHER_PDF_DIR", "data/pdfs")
LOG_PATH = os.environ.get("IPO_WATCHER_LOG_PATH", "logs/ipo_watcher.log")
