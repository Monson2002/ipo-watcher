import smtplib
from email.message import EmailMessage

from . import config


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
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def send_alert(rows):
    """Sends one batched email for all rows. Returns True on success, False
    on any SMTP/network failure — caller decides whether to retry (by not
    marking these filings as notified)."""
    if not rows:
        return True
    msg = EmailMessage()
    msg["Subject"] = build_email_subject(rows)
    msg["From"] = config.ALERT_FROM
    msg["To"] = config.ALERT_TO
    msg.set_content(build_email_body(rows))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False
