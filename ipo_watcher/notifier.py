import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger("ipo_watcher.notifier")


def build_email_subject(rows):
    count = len(rows)
    plural = "" if count == 1 else "s"
    return f"IPO Watcher: {count} new SEBI filing{plural}"


def build_email_body(rows):
    lines = []
    for r in rows:
        header = "New company" if r["is_first_filing"] else "Existing company"
        lines.append(f"{header}: {r['company_name']}")
        lines.append(f"  {r['filing_type']} filed {r['filing_date']}")
        lines.append(f"  {r['document_url']}")
        lines.append(f"  (source: {r['source_page_url']})")
        lines.append(f"  {r['gmp_prediction']}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.ALERT_FROM
    msg["To"] = config.ALERT_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("SMTP send failed: %s", exc)
        return False


def send_alert(rows):
    """Sends one batched email for all rows. Returns True on success, False
    on any SMTP/network failure — caller decides whether to retry (by not
    marking these filings as notified)."""
    if not rows:
        return True
    return _send_email(build_email_subject(rows), build_email_body(rows))


def build_gmp_update_subject(rows):
    count = len(rows)
    plural = "" if count == 1 else "s"
    return f"IPO Watcher: {count} GMP update{plural}"


def build_gmp_update_body(rows):
    lines = []
    for r in rows:
        lines.append(
            f"{r['company_name']}: GMP moved from {r['old_pct']:.1f}% to {r['new_pct']:.1f}%"
        )
        lines.append(r["prediction_text"])
        if r["closing_today"]:
            lines.append(
                "Bidding closes today - this is the last day to apply or revoke an application."
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def send_gmp_update_alert(rows):
    """Sends one batched email for all GMP movement rows. Same
    True/False-on-failure contract as send_alert."""
    if not rows:
        return True
    return _send_email(build_gmp_update_subject(rows), build_gmp_update_body(rows))
