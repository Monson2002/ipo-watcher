import html
import logging

import requests

from . import config

log = logging.getLogger("ipo_watcher.notifier")

_HTML_WRAPPER = """<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
    <div style="max-width:640px;margin:0 auto;background:#ffffff;border-radius:8px;padding:24px;">
      <h2 style="margin:0 0 4px;color:#111827;font-size:20px;">IPO Watcher</h2>
      <p style="margin:0 0 16px;color:#6b7280;font-size:13px;">{subtitle}</p>
      {cards}
    </div>
  </body>
</html>"""


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


def build_email_html(rows):
    cards = []
    for r in rows:
        header = "New company" if r["is_first_filing"] else "Existing company"
        accent = "#2563eb" if r["is_first_filing"] else "#6b7280"
        cards.append(f"""
      <div style="border-bottom:1px solid #e5e7eb;padding:16px 0;">
        <div style="font-size:12px;font-weight:600;color:{accent};text-transform:uppercase;letter-spacing:0.04em;">{html.escape(header)}</div>
        <div style="font-size:16px;font-weight:600;color:#111827;margin:4px 0 2px;">{html.escape(r['company_name'])}</div>
        <div style="font-size:14px;color:#374151;">{html.escape(r['filing_type'])} filed {html.escape(r['filing_date'])}</div>
        <div style="font-size:14px;margin-top:8px;"><a href="{html.escape(r['document_url'])}" style="color:#2563eb;text-decoration:none;">View filing PDF</a></div>
        <div style="font-size:12px;color:#9ca3af;margin-top:4px;">source: <a href="{html.escape(r['source_page_url'])}" style="color:#9ca3af;">{html.escape(r['source_page_url'])}</a></div>
        <div style="font-size:14px;color:#047857;margin-top:10px;white-space:pre-line;">{html.escape(r['gmp_prediction'])}</div>
      </div>""")
    count = len(rows)
    subtitle = f"{count} new SEBI filing{'' if count == 1 else 's'}"
    return _HTML_WRAPPER.format(subtitle=subtitle, cards="".join(cards))


def _send_email(subject, text_body, html_body):
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={
                "from": config.ALERT_FROM,
                "to": [config.ALERT_TO],
                "subject": subject,
                "text": text_body,
                "html": html_body,
            },
            timeout=30,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        response = getattr(exc, "response", None)
        detail = f" - {response.text}" if response is not None else ""
        log.warning("Resend send failed: %s%s", exc, detail)
        return False


def send_alert(rows):
    """Sends one batched email for all rows. Returns True on success, False
    on any send failure — caller decides whether to retry (by not marking
    these filings as notified)."""
    if not rows:
        return True
    return _send_email(
        build_email_subject(rows), build_email_body(rows), build_email_html(rows)
    )


def build_gmp_update_subject(rows):
    count = len(rows)
    plural = "" if count == 1 else "s"
    return f"IPO Watcher: {count} GMP update{plural}"


def _gmp_movement_fragment(r):
    """No baseline (old_pct is None) means GMP just became available for
    the first time for a company already notified about via a filing —
    there's no 'move' to report yet, just the fact that data now exists."""
    if r["old_pct"] is None:
        return f"GMP now available at {r['new_pct']:.1f}%"
    return f"GMP moved from {r['old_pct']:.1f}% to {r['new_pct']:.1f}%"


def build_gmp_update_body(rows):
    lines = []
    for r in rows:
        lines.append(f"{r['company_name']}: {_gmp_movement_fragment(r)}")
        lines.append(r["prediction_text"])
        if r["closing_today"]:
            lines.append(
                "Bidding closes today - this is the last day to apply or revoke an application."
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_gmp_update_html(rows):
    cards = []
    for r in rows:
        cards.append(f"""
      <div style="border-bottom:1px solid #e5e7eb;padding:16px 0;">
        <div style="font-size:16px;font-weight:600;color:#111827;margin:0 0 4px;">{html.escape(r['company_name'])}</div>
        <div style="font-size:14px;color:#374151;">{html.escape(_gmp_movement_fragment(r))}</div>
        <div style="font-size:14px;color:#047857;margin-top:8px;white-space:pre-line;">{html.escape(r['prediction_text'])}</div>
        {'<div style="font-size:13px;color:#b45309;margin-top:8px;font-weight:600;">Bidding closes today - this is the last day to apply or revoke an application.</div>' if r['closing_today'] else ''}
      </div>""")
    count = len(rows)
    subtitle = f"{count} GMP update{'' if count == 1 else 's'}"
    return _HTML_WRAPPER.format(subtitle=subtitle, cards="".join(cards))


def send_gmp_update_alert(rows):
    """Sends one batched email for all GMP movement rows. Same
    True/False-on-failure contract as send_alert."""
    if not rows:
        return True
    return _send_email(
        build_gmp_update_subject(rows), build_gmp_update_body(rows), build_gmp_update_html(rows)
    )
