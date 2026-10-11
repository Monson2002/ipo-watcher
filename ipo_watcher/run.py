import datetime
import logging
import logging.handlers
import os
import time

from . import config, db, sebi_source, downloader, notifier, scoring

MAX_PAGES = 20  # safety cap; normal runs stop once a page yields nothing new
REQUEST_DELAY_SECONDS = 0.5  # courtesy delay between outbound requests
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
        return "GMP not yet available."
    return scoring.format_prediction(scoring.predict_listing_gain(gmp_pct))


def send_pending_alert(conn):
    rows = db.get_unnotified_filings(conn)
    if not rows:
        log.info("No new filings to alert on")
        return
    enriched_rows = []
    for r in rows:
        row = dict(r)
        row["gmp_prediction"] = _gmp_prediction_text(conn, row["company_id"])
        enriched_rows.append(row)
    ok = notifier.send_alert(enriched_rows)
    if ok:
        db.mark_notified(conn, [r["id"] for r in rows])
        log.info("Sent alert email for %d filing(s)", len(rows))
    else:
        log.error("Failed to send alert email for %d filing(s); will retry next run", len(rows))


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
