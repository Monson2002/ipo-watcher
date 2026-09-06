from unittest.mock import patch
from ipo_watcher import db, run

ONE_ROW_LISTING = """
<table id='sample_1'><tbody>
<tr role='row'><td>Sep 05, 2026</td>
<td><a href="https://www.sebi.gov.in/filings/public-issues/sep-2026/test-co-drhp_1.html" target="_blank" title="Test Co - DRHP" class="points"> Test Co - DRHP</a>
</tr>
</tbody></table>
"""
EMPTY_LISTING = "<table id='sample_1'><tbody></tbody></table>"
DETAIL_HTML = "<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.pdf'></iframe>"


def test_discover_download_and_alert_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(run.config, "PDF_DIR", str(tmp_path / "pdfs"))
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_db(conn)

    with patch("ipo_watcher.run.sebi_source.fetch_listing_page", side_effect=[ONE_ROW_LISTING, EMPTY_LISTING]), \
         patch("ipo_watcher.run.sebi_source.fetch_detail_page", return_value=DETAIL_HTML):
        run.discover_new_filings(conn)

    filings = conn.execute("SELECT * FROM filings").fetchall()
    assert len(filings) == 1
    assert filings[0]["document_url"] == "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.pdf"

    with patch("ipo_watcher.run.downloader.download_pdf", return_value="deadbeef") as mock_download:
        run.download_pending_pdfs(conn)
    mock_download.assert_called_once()
    filing = conn.execute("SELECT * FROM filings").fetchone()
    assert filing["pdf_path"] is not None
    assert filing["pdf_sha256"] == "deadbeef"

    with patch("ipo_watcher.run.notifier.send_alert", return_value=True) as mock_send:
        run.send_pending_alert(conn)
    mock_send.assert_called_once()
    sent_rows = mock_send.call_args[0][0]
    assert sent_rows[0]["company_name"] == "Test Co"
    assert db.get_unnotified_filings(conn) == []

    conn.close()


def test_discover_new_filings_skips_detail_fetch_for_known_rows(tmp_path):
    conn = db.connect(str(tmp_path / "test3.db"))
    db.init_db(conn)

    with patch(
        "ipo_watcher.run.sebi_source.fetch_listing_page",
        side_effect=[ONE_ROW_LISTING, EMPTY_LISTING, ONE_ROW_LISTING],
    ), patch(
        "ipo_watcher.run.sebi_source.fetch_detail_page", return_value=DETAIL_HTML
    ) as mock_detail:
        run.discover_new_filings(conn)  # first run: row is new, page 2 is empty -> stop
        run.discover_new_filings(conn)  # second run: same row, already known -> no page 2 fetch

    filings = conn.execute("SELECT * FROM filings").fetchall()
    assert len(filings) == 1
    mock_detail.assert_called_once()

    conn.close()


def test_send_pending_alert_does_not_mark_notified_on_send_failure(tmp_path):
    conn = db.connect(str(tmp_path / "test2.db"))
    db.init_db(conn)
    db.upsert_filing(
        conn, company_name="Fails Co", filing_type="DRHP", filing_date="2026-09-05",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    with patch("ipo_watcher.run.notifier.send_alert", return_value=False):
        run.send_pending_alert(conn)
    assert len(db.get_unnotified_filings(conn)) == 1
    conn.close()
