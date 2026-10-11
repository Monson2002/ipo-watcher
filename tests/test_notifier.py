from ipo_watcher import notifier

ROWS = [
    {
        "company_name": "Example Textiles Ltd",
        "filing_type": "DRHP",
        "filing_date": "2026-09-05",
        "document_url": "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.pdf",
        "source_page_url": "https://www.sebi.gov.in/filings/public-issues/sep-2026/example-textiles-drhp_1.html",
        "is_first_filing": 1,
        "gmp_prediction": "Predicted Listing Gain: +18.5%\n...",
    },
    {
        "company_name": "Acme Robotics Ltd",
        "filing_type": "Corrigendum to DRHP",
        "filing_date": "2026-09-05",
        "document_url": "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/2.pdf",
        "source_page_url": "https://www.sebi.gov.in/filings/public-issues/sep-2026/acme-corrigendum_2.html",
        "is_first_filing": 0,
        "gmp_prediction": "GMP not yet available.",
    },
]


def test_build_email_subject():
    assert notifier.build_email_subject(ROWS) == "IPO Watcher: 2 new SEBI filings"
    assert notifier.build_email_subject(ROWS[:1]) == "IPO Watcher: 1 new SEBI filing"


def test_build_email_body_labels_new_vs_existing_company():
    body = notifier.build_email_body(ROWS)
    assert "New company: Example Textiles Ltd" in body
    assert "  DRHP filed 2026-09-05" in body
    assert "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.pdf" in body
    assert "Existing company: Acme Robotics Ltd" in body
    assert "  Corrigendum to DRHP filed 2026-09-05" in body


def test_build_email_body_includes_gmp_prediction_per_row():
    body = notifier.build_email_body(ROWS)
    assert "Predicted Listing Gain: +18.5%" in body
    assert "GMP not yet available." in body


from unittest.mock import patch, MagicMock


def test_send_alert_returns_true_and_uses_smtp(monkeypatch):
    monkeypatch.setattr(notifier.config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifier.config, "SMTP_PORT", 587)
    monkeypatch.setattr(notifier.config, "SMTP_USER", "me@example.com")
    monkeypatch.setattr(notifier.config, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "me@example.com")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_smtp)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("ipo_watcher.notifier.smtplib.SMTP", return_value=fake_smtp) as mock_smtp_cls:
        ok = notifier.send_alert(ROWS)

    assert ok is True
    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=30)
    fake_smtp.starttls.assert_called_once()
    fake_smtp.login.assert_called_once_with("me@example.com", "secret")
    fake_smtp.send_message.assert_called_once()


def test_send_alert_returns_false_on_smtp_error(monkeypatch):
    monkeypatch.setattr(notifier.config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifier.config, "SMTP_PORT", 587)

    with patch("ipo_watcher.notifier.smtplib.SMTP", side_effect=OSError("boom")):
        ok = notifier.send_alert(ROWS)

    assert ok is False


def test_send_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.smtplib.SMTP") as mock_smtp_cls:
        ok = notifier.send_alert([])
    assert ok is True
    mock_smtp_cls.assert_not_called()


GMP_UPDATE_ROWS = [
    {
        "company_name": "Acme Robotics Ltd",
        "old_pct": 12.0,
        "new_pct": 18.5,
        "prediction_text": "Predicted Listing Gain: +18.9%\n...",
        "closing_today": False,
    },
    {
        "company_name": "Gaurik Fashions Limited",
        "old_pct": 20.0,
        "new_pct": 14.0,
        "prediction_text": "Predicted Listing Gain: +14.3%\n...",
        "closing_today": True,
    },
]


def test_build_gmp_update_subject():
    assert notifier.build_gmp_update_subject(GMP_UPDATE_ROWS) == "IPO Watcher: 2 GMP updates"
    assert notifier.build_gmp_update_subject(GMP_UPDATE_ROWS[:1]) == "IPO Watcher: 1 GMP update"


def test_build_gmp_update_body_shows_movement_and_prediction():
    body = notifier.build_gmp_update_body(GMP_UPDATE_ROWS)
    assert "Acme Robotics Ltd: GMP moved from 12.0% to 18.5%" in body
    assert "Predicted Listing Gain: +18.9%" in body


def test_build_gmp_update_body_includes_closing_today_note_only_when_true():
    body = notifier.build_gmp_update_body(GMP_UPDATE_ROWS)
    assert "Gaurik Fashions Limited: GMP moved from 20.0% to 14.0%" in body
    assert body.count("Bidding closes today") == 1


def test_send_gmp_update_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.smtplib.SMTP") as mock_smtp_cls:
        ok = notifier.send_gmp_update_alert([])
    assert ok is True
    mock_smtp_cls.assert_not_called()


def test_send_gmp_update_alert_uses_smtp(monkeypatch):
    monkeypatch.setattr(notifier.config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifier.config, "SMTP_PORT", 587)
    monkeypatch.setattr(notifier.config, "SMTP_USER", "me@example.com")
    monkeypatch.setattr(notifier.config, "SMTP_PASSWORD", "secret")
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "me@example.com")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_smtp)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("ipo_watcher.notifier.smtplib.SMTP", return_value=fake_smtp):
        ok = notifier.send_gmp_update_alert(GMP_UPDATE_ROWS)

    assert ok is True
    fake_smtp.send_message.assert_called_once()
