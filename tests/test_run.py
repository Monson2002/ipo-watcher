import datetime
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
    assert sent_rows[0]["gmp_prediction"] == "GMP not yet available."
    assert db.get_unnotified_filings(conn) == []

    conn.close()


def test_send_pending_alert_includes_predicted_gain_when_gmp_available(tmp_path):
    conn = db.connect(str(tmp_path / "test4.db"))
    db.init_db(conn)
    db.upsert_filing(
        conn, company_name="Priced Co", filing_type="DRHP", filing_date="2026-09-05",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    company_id, _ = db._get_or_create_company(conn, "Priced Co")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.0)

    with patch("ipo_watcher.run.notifier.send_alert", return_value=True) as mock_send:
        run.send_pending_alert(conn)
    sent_rows = mock_send.call_args[0][0]
    assert "Predicted Listing Gain: +18.5%" in sent_rows[0]["gmp_prediction"]

    conn.close()


def test_send_pending_alert_records_gmp_notification_baseline(tmp_path):
    conn = db.connect(str(tmp_path / "test5.db"))
    db.init_db(conn)
    db.upsert_filing(
        conn, company_name="Priced Co", filing_type="DRHP", filing_date="2026-09-05",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    company_id, _ = db._get_or_create_company(conn, "Priced Co")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.0)

    with patch("ipo_watcher.run.notifier.send_alert", return_value=True):
        run.send_pending_alert(conn)

    assert db.get_last_notified_gmp_pct(conn, company_id) == 18.0
    conn.close()


def test_send_pending_alert_does_not_record_baseline_when_send_fails(tmp_path):
    conn = db.connect(str(tmp_path / "test7.db"))
    db.init_db(conn)
    db.upsert_filing(
        conn, company_name="Flaky SMTP Co", filing_type="DRHP", filing_date="2026-09-05",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    company_id, _ = db._get_or_create_company(conn, "Flaky SMTP Co")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.0)

    with patch("ipo_watcher.run.notifier.send_alert", return_value=False):
        run.send_pending_alert(conn)

    assert db.get_last_notified_gmp_pct(conn, company_id) is None
    conn.close()


def test_send_pending_alert_does_not_record_baseline_when_gmp_not_available(tmp_path):
    conn = db.connect(str(tmp_path / "test6.db"))
    db.init_db(conn)
    db.upsert_filing(
        conn, company_name="No GMP Co", filing_type="DRHP", filing_date="2026-09-05",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    company_id, _ = db._get_or_create_company(conn, "No GMP Co")

    with patch("ipo_watcher.run.notifier.send_alert", return_value=True):
        run.send_pending_alert(conn)

    assert db.get_last_notified_gmp_pct(conn, company_id) is None
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


def _seed_company_with_history(conn, name, last_notified_pct, latest_gmp_pct, close_date=None, is_listed=None):
    company_id, _ = db._get_or_create_company(conn, name)
    db.record_gmp_notification(conn, company_id, last_notified_pct)
    db.record_gmp_snapshot(conn, company_id, latest_gmp_pct, close_date=close_date, is_listed=is_listed)
    return company_id


def test_send_gmp_update_alerts_sends_when_threshold_crossed(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update1.db"))
    db.init_db(conn)
    company_id = _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=16.0)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_called_once()
    rows = mock_send.call_args[0][0]
    assert rows[0]["company_name"] == "Acme Robotics Ltd"
    assert rows[0]["old_pct"] == 10.0
    assert rows[0]["new_pct"] == 16.0
    assert db.get_last_notified_gmp_pct(conn, company_id) == 16.0
    conn.close()


def test_send_gmp_update_alerts_sends_on_exact_threshold_move_despite_float_rounding(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update_boundary.db"))
    db.init_db(conn)
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=4.20, latest_gmp_pct=9.20)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_called_once()
    conn.close()


def test_send_gmp_update_alerts_triggers_on_decrease_too(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update2.db"))
    db.init_db(conn)
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=20.0, latest_gmp_pct=14.0)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    rows = mock_send.call_args[0][0]
    assert rows[0]["old_pct"] == 20.0
    assert rows[0]["new_pct"] == 14.0
    conn.close()


def test_send_gmp_update_alerts_skips_below_threshold_and_does_not_reset_baseline(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update3.db"))
    db.init_db(conn)
    company_id = _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=13.0)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_not_called()
    assert db.get_last_notified_gmp_pct(conn, company_id) == 10.0
    conn.close()


def test_send_gmp_update_alerts_batches_multiple_companies_into_one_email(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update4.db"))
    db.init_db(conn)
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=16.0)
    _seed_company_with_history(conn, "Gaurik Fashions Limited", last_notified_pct=30.0, latest_gmp_pct=22.0)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_called_once()
    rows = mock_send.call_args[0][0]
    assert len(rows) == 2
    conn.close()


def test_send_gmp_update_alerts_stops_once_listed(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update5.db"))
    db.init_db(conn)
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=30.0, is_listed=True)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_not_called()
    conn.close()


def test_send_gmp_update_alerts_stops_once_close_date_has_passed(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update6.db"))
    db.init_db(conn)
    yesterday = (datetime.datetime.now(db.config.IST).date() - datetime.timedelta(days=1)).isoformat()
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=30.0, close_date=yesterday)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_not_called()
    conn.close()


def test_send_gmp_update_alerts_includes_closing_today_note_when_close_date_is_today(tmp_path):
    conn = db.connect(str(tmp_path / "gmp_update7.db"))
    db.init_db(conn)
    today = datetime.datetime.now(db.config.IST).date().isoformat()
    _seed_company_with_history(conn, "Acme Robotics Ltd", last_notified_pct=10.0, latest_gmp_pct=16.0, close_date=today)

    with patch("ipo_watcher.run.notifier.send_gmp_update_alert", return_value=True) as mock_send:
        run.send_gmp_update_alerts(conn)

    mock_send.assert_called_once()
    rows = mock_send.call_args[0][0]
    assert rows[0]["closing_today"] is True
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
