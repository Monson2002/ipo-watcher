import datetime
import logging
import logging.handlers
import os
import time

from . import config, db, sebi_source, downloader, notifier, scoring

MAX_PAGES = 20  # safety cap; normal runs stop once a page yields nothing new
REQUEST_DELAY_SECONDS = 0.5  # courtesy delay between outbound requests
GMP_MOVEMENT_THRESHOLD = 5.0  # percentage points, absolute
log = logging.getLogger("ipo_watcher.run")


class _ISTFormatter(logging.Formatter):
    """Renders log timestamps in IST regardless of the machine's local timezone."""

    def formatTime(self, record, datefmt=None):
        dt = datetime.datetime.fromtimestamp(record.created, tz=config.IST)
        return dt.isoformat(timespec="seconds")


def setup_logging():
    os.makedirs(os.path.dirname(config.LOG_PATH) or ".", exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(_ISTFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _resolve_document_url(row):
    detail_html = sebi_source.fetch_detail_page(row.source_page_url)
    return sebi_source.parse_detail(detail_html)


def discover_new_filings(conn):
    page_no = 1
    while page_no <= MAX_PAGES:
        try:
            html = sebi_source.fetch_listing_page(page_no)
        except Exception:
            log.exception("Failed to fetch listing page %s", page_no)
            break
        rows = sebi_source.parse_listing(html)
        if not rows:
            if page_no == 1:
                log.warning("Listing page 1 parsed to zero rows; parser or site may be broken")
            break
        any_new = False
        newly_inserted = 0
        for row in rows:
            existing = conn.execute(
                "SELECT 1 FROM filings WHERE source_page_url = ?", (row.source_page_url,)
            ).fetchone()
            if existing:
                continue
            try:
                document_url = _resolve_document_url(row)
            except Exception:
                log.exception("Failed to fetch/parse detail page %s", row.source_page_url)
                continue
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)
            if document_url is None:
                log.warning("No PDF found on detail page %s", row.source_page_url)
                continue
            result = db.upsert_filing(
                conn,
                company_name=row.company_name,
                filing_type=row.filing_type,
                filing_date=row.filing_date,
                document_url=document_url,
                source_page_url=row.source_page_url,
            )
            if result["is_new_filing"]:
                any_new = True
                newly_inserted += 1
        log.info(
            "Listing page %s: parsed %d row(s), %d newly inserted",
            page_no, len(rows), newly_inserted,
        )
        if not any_new:
            break
        page_no += 1


def download_pending_pdfs(conn):
    downloaded = 0
    for filing in db.get_filings_needing_download(conn):
        dest_path = downloader.build_pdf_path(
            config.PDF_DIR,
            filing["company_name"],
            filing["filing_date"],
            filing["filing_type"],
            filing["dedup_key"],
        )
        try:
            sha256 = downloader.download_pdf(filing["document_url"], dest_path)
        except Exception:
            log.exception("Failed to download PDF for filing id %s", filing["id"])
            continue
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)
        db.set_pdf_info(conn, filing["id"], dest_path, sha256)
        downloaded += 1
    log.info("Downloaded %d PDF(s)", downloaded)


def _gmp_prediction_text(conn, company_id):
    gmp_pct = db.get_latest_gmp_pct(conn, company_id)
    if gmp_pct is None:
        return None, "GMP not yet available."
    return gmp_pct, scoring.format_prediction(scoring.predict_listing_gain(gmp_pct))


def send_pending_alert(conn):
    rows = db.get_unnotified_filings(conn)
    if not rows:
        log.info("No new filings to alert on")
        return
    enriched_rows = []
    pending_notifications = []
    for r in rows:
        row = dict(r)
        gmp_pct, prediction_text = _gmp_prediction_text(conn, row["company_id"])
        row["gmp_prediction"] = prediction_text
        if gmp_pct is not None:
            pending_notifications.append((row["company_id"], gmp_pct))
        enriched_rows.append(row)
    ok = notifier.send_alert(enriched_rows)
    if ok:
        db.mark_notified(conn, [r["id"] for r in rows])
        for company_id, gmp_pct in pending_notifications:
            db.record_gmp_notification(conn, company_id, gmp_pct)
        log.info("Sent alert email for %d filing(s)", len(rows))
    else:
        log.error("Failed to send alert email for %d filing(s); will retry next run", len(rows))


def send_gmp_update_alerts(conn):
    today = datetime.datetime.now(config.IST).date().isoformat()
    rows = []
    for company_id in db.get_notified_company_ids(conn):
        is_listed = db.get_latest_is_listed(conn, company_id)
        close_date = db.get_latest_close_date(conn, company_id)
        if is_listed or (close_date is not None and close_date < today):
            continue
        last_notified = db.get_last_notified_gmp_pct(conn, company_id)
        latest_pct = db.get_latest_gmp_pct(conn, company_id)
        if latest_pct is None:
            continue
        if round(abs(latest_pct - last_notified), 6) < GMP_MOVEMENT_THRESHOLD:
            continue
        company_row = conn.execute(
            "SELECT name FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        rows.append({
            "company_name": company_row["name"],
            "old_pct": last_notified,
            "new_pct": latest_pct,
            "prediction_text": scoring.format_prediction(scoring.predict_listing_gain(latest_pct)),
            "closing_today": close_date == today,
        })
        db.record_gmp_notification(conn, company_id, latest_pct)
    if rows:
        plural = "y" if len(rows) == 1 else "ies"
        ok = notifier.send_gmp_update_alert(rows)
        if ok:
            log.info("Sent GMP update alert for %d compan%s", len(rows), plural)
        else:
            log.error("Failed to send GMP update alert for %d compan%s", len(rows), plural)


def main():
    setup_logging()
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    try:
        discover_new_filings(conn)
        download_pending_pdfs(conn)
        send_pending_alert(conn)
    except Exception:
        log.exception("Unhandled error in ipo_watcher run")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
