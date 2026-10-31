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
import requests


def test_send_alert_returns_true_and_posts_to_resend(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response) as mock_post:
        ok = notifier.send_alert(ROWS)

    assert ok is True
    mock_post.assert_called_once_with(
        "https://api.resend.com/emails",
        headers={"Authorization": "Bearer re_test_key"},
        json={
            "from": "onboarding@resend.dev",
            "to": ["me@example.com"],
            "subject": notifier.build_email_subject(ROWS),
            "text": notifier.build_email_body(ROWS),
        },
        timeout=30,
    )
    fake_response.raise_for_status.assert_called_once()


def test_send_alert_returns_false_on_connection_error(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    with patch(
        "ipo_watcher.notifier.requests.post",
        side_effect=requests.exceptions.ConnectionError("boom"),
    ):
        ok = notifier.send_alert(ROWS)

    assert ok is False


def test_send_alert_returns_false_on_non_2xx_response(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "someone-else@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "403 Client Error: Forbidden"
    )

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response):
        ok = notifier.send_alert(ROWS)

    assert ok is False


def test_send_email_logs_resend_response_body_on_http_error(monkeypatch, caplog):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "someone-else@example.com")

    fake_response = MagicMock()
    fake_response.text = '{"message": "You can only send testing emails to your own email address"}'
    http_error = requests.exceptions.HTTPError("403 Client Error: Forbidden")
    http_error.response = fake_response
    fake_response.raise_for_status.side_effect = http_error

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response):
        with caplog.at_level("WARNING"):
            ok = notifier.send_alert(ROWS)

    assert ok is False
    assert "own email address" in caplog.text


def test_send_email_logs_without_response_body_on_connection_error(monkeypatch, caplog):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    with patch(
        "ipo_watcher.notifier.requests.post",
        side_effect=requests.exceptions.ConnectionError("boom"),
    ):
        with caplog.at_level("WARNING"):
            ok = notifier.send_alert(ROWS)

    assert ok is False
    assert "Resend send failed" in caplog.text


def test_send_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.requests.post") as mock_post:
        ok = notifier.send_alert([])
    assert ok is True
    mock_post.assert_not_called()


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
    with patch("ipo_watcher.notifier.requests.post") as mock_post:
        ok = notifier.send_gmp_update_alert([])
    assert ok is True
    mock_post.assert_not_called()


def test_send_gmp_update_alert_posts_to_resend(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response) as mock_post:
        ok = notifier.send_gmp_update_alert(GMP_UPDATE_ROWS)

    assert ok is True
    mock_post.assert_called_once()
