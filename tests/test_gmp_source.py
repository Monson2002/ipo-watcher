from unittest.mock import MagicMock, patch

import pytest
import requests

from ipo_watcher import db
from ipo_watcher.gmp_source import (
    GmpRow,
    fetch_gmp_listing,
    match_company,
    parse_gmp_listing,
    poll_gmp_snapshots,
)


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _default_api_key(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")


def _envelope(entries):
    return {"success": True, "plan": "standard", "count": len(entries), "data": entries}


def test_parse_gmp_listing_normal_row():
    response = _envelope([
        {
            "slug": "acme-robotics",
            "name": "Acme Robotics Ltd",
            "display_name": "Acme Robotics",
            "status": "Open",
            "gmp": {
                "price": "7.5",
                "percentage": "5.07",
                "estimated_listing_price": 155.5,
                "updated_at_label": "22 Sep 2026, 12:36 PM IST",
            },
        }
    ])
    rows = parse_gmp_listing(response)
    assert rows == [GmpRow(company_name="Acme Robotics Ltd", gmp_pct=5.07, gmp_rupees=7.5)]


def test_parse_gmp_listing_skips_entry_with_no_gmp_key():
    response = _envelope([
        {"slug": "upcoming-co", "name": "Upcoming Co Ltd", "status": "Upcoming"},
    ])
    assert parse_gmp_listing(response) == []


def test_parse_gmp_listing_skips_entry_with_unparseable_percentage():
    response = _envelope([
        {"name": "Bad Data Ltd", "gmp": {"price": "7.5", "percentage": "N/A"}},
    ])
    assert parse_gmp_listing(response) == []


def test_parse_gmp_listing_ignores_unknown_fields():
    response = _envelope([
        {
            "name": "Future Fields Ltd",
            "gmp": {"price": "10", "percentage": "8.0"},
            "some_new_field_ipo_guru_adds_later": {"nested": True},
        }
    ])
    rows = parse_gmp_listing(response)
    assert rows == [GmpRow(company_name="Future Fields Ltd", gmp_pct=8.0, gmp_rupees=10.0)]


def test_parse_gmp_listing_handles_missing_gmp_rupees_price():
    response = _envelope([
        {"name": "Percent Only Ltd", "gmp": {"percentage": "3.2"}},
    ])
    rows = parse_gmp_listing(response)
    assert rows == [GmpRow(company_name="Percent Only Ltd", gmp_pct=3.2, gmp_rupees=None)]


def test_parse_gmp_listing_skips_entry_with_nan_or_infinite_percentage():
    response = _envelope([
        {"name": "Nan Co Ltd", "gmp": {"percentage": "nan"}},
        {"name": "Inf Co Ltd", "gmp": {"percentage": "inf"}},
    ])
    assert parse_gmp_listing(response) == []


def test_parse_gmp_listing_skips_entry_with_missing_name():
    response = _envelope([
        {"gmp": {"percentage": "5.0", "price": "7.0"}},
    ])
    assert parse_gmp_listing(response) == []


def test_parse_gmp_listing_handles_data_key_being_none():
    response = {"success": False, "error": "missing_key", "data": None}
    assert parse_gmp_listing(response) == []


def test_parse_gmp_listing_reads_close_date_and_is_listed():
    response = _envelope([
        {
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "5.07", "price": "7.5"},
            "close_date": "2026-10-20",
            "is_listed": False,
        }
    ])
    rows = parse_gmp_listing(response)
    assert rows == [
        GmpRow(
            company_name="Acme Robotics Ltd",
            gmp_pct=5.07,
            gmp_rupees=7.5,
            close_date="2026-10-20",
            is_listed=False,
        )
    ]


def test_parse_gmp_listing_treats_unexpected_is_listed_type_as_none():
    response = _envelope([
        {"name": "Weird Co Ltd", "gmp": {"percentage": "5.0"}, "is_listed": "yes please"},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].is_listed is None


def test_parse_gmp_listing_accepts_string_booleans_for_is_listed():
    response = _envelope([
        {"name": "String True Ltd", "gmp": {"percentage": "5.0"}, "is_listed": "true"},
        {"name": "String False Ltd", "gmp": {"percentage": "5.0"}, "is_listed": "false"},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].is_listed is True
    assert rows[1].is_listed is False


def test_parse_gmp_listing_treats_malformed_close_date_as_none():
    response = _envelope([
        {"name": "Bad Date Ltd", "gmp": {"percentage": "5.0"}, "close_date": "20-Oct-2026"},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].close_date is None


def test_parse_gmp_listing_defaults_close_date_and_is_listed_to_none_when_absent():
    response = _envelope([
        {"name": "Upcoming Co Ltd", "gmp": {"percentage": "5.0"}},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].close_date is None
    assert rows[0].is_listed is None


def test_match_company_exact_match(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert match_company(conn, "Acme Robotics Ltd") == company_id


def test_match_company_no_match_returns_none(conn):
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert match_company(conn, "Totally Unrelated Co") is None


def test_match_company_matches_despite_case_and_whitespace(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert match_company(conn, "  ACME ROBOTICS LTD  ") == company_id


def test_match_company_matches_despite_suffix_difference(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert match_company(conn, "Acme Robotics") == company_id


def test_match_company_matches_limited_suffix_specifically(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Limited")
    assert match_company(conn, "Acme Robotics Ltd") == company_id


def test_match_company_picks_lowest_id_on_normalization_collision(conn, caplog):
    first_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db._get_or_create_company(conn, "Acme Robotics Limited")
    assert match_company(conn, "Acme Robotics Pvt") == first_id
    assert "Multiple companies normalize" in caplog.text


def test_fetch_gmp_listing_sends_api_key_header_and_returns_json(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")
    fake_response = MagicMock()
    fake_response.json.return_value = {"success": True, "data": []}
    fake_response.raise_for_status = MagicMock()
    with patch("ipo_watcher.gmp_source.requests.get", return_value=fake_response) as mock_get:
        result = fetch_gmp_listing()
    assert result == {"success": True, "data": []}
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["X-API-KEY"] == "test-key-123"


def test_fetch_gmp_listing_retries_on_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")
    fake_response = MagicMock()
    fake_response.json.return_value = {"success": True, "data": []}
    fake_response.raise_for_status = MagicMock()
    with patch(
        "ipo_watcher.gmp_source.requests.get",
        side_effect=[requests.ConnectionError("boom"), fake_response],
    ) as mock_get, patch("ipo_watcher.gmp_source.time.sleep"):
        result = fetch_gmp_listing()
    assert result == {"success": True, "data": []}
    assert mock_get.call_count == 2


def test_fetch_gmp_listing_does_not_retry_on_invalid_url_error(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")
    with patch(
        "ipo_watcher.gmp_source.requests.get",
        side_effect=requests.exceptions.InvalidURL("bad url"),
    ) as mock_get:
        with pytest.raises(requests.exceptions.InvalidURL):
            fetch_gmp_listing()
    assert mock_get.call_count == 1


def test_fetch_gmp_listing_does_not_retry_on_http_error(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = requests.HTTPError("429 Client Error")
    with patch("ipo_watcher.gmp_source.requests.get", return_value=fake_response) as mock_get:
        with pytest.raises(requests.HTTPError):
            fetch_gmp_listing()
    assert mock_get.call_count == 1


def test_poll_gmp_snapshots_all_matched(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [{"name": "Acme Robotics Ltd", "gmp": {"percentage": "5.07", "price": "7.5"}}],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result == {"matched": 1, "unmatched": 0, "skipped_errors": 0}
    assert db.get_latest_gmp_pct(conn, company_id) == 5.07


def test_poll_gmp_snapshots_partial_match(conn):
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [
            {"name": "Acme Robotics Ltd", "gmp": {"percentage": "5.0", "price": "7.0"}},
            {"name": "Totally Unknown Co", "gmp": {"percentage": "9.0", "price": "12.0"}},
        ],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result == {"matched": 1, "unmatched": 1, "skipped_errors": 0}
    assert conn.execute("SELECT COUNT(*) AS n FROM gmp_snapshots").fetchone()["n"] == 1


def test_poll_gmp_snapshots_does_not_cross_attribute_between_companies(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    fake_json = {
        "success": True,
        "data": [
            {"name": "Acme Robotics Ltd", "gmp": {"percentage": "5.0", "price": "7.0"}},
            {"name": "Gaurik Fashions Limited", "gmp": {"percentage": "20.0", "price": "40.0"}},
        ],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        poll_gmp_snapshots(conn)
    assert db.get_latest_gmp_pct(conn, company_a) == 5.0
    assert db.get_latest_gmp_pct(conn, company_b) == 20.0


def test_poll_gmp_snapshots_counts_skipped_errors_for_malformed_rows(conn):
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [
            {"name": "Acme Robotics Ltd", "gmp": {"percentage": "5.0", "price": "7.0"}},
            {"name": "Bad Data Ltd", "gmp": {"percentage": "N/A"}},
            {"gmp": {"percentage": "9.0"}},
        ],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result == {"matched": 1, "unmatched": 0, "skipped_errors": 2}


def test_poll_gmp_snapshots_passes_close_date_and_is_listed_through(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [{
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "5.0", "price": "7.0"},
            "close_date": "2026-10-20",
            "is_listed": False,
        }],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        poll_gmp_snapshots(conn)
    assert db.get_latest_close_date(conn, company_id) == "2026-10-20"
    assert db.get_latest_is_listed(conn, company_id) is False


def test_poll_gmp_snapshots_skips_cleanly_when_api_key_missing(conn, monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "")
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    with patch("ipo_watcher.gmp_source.requests.get") as mock_get:
        result = poll_gmp_snapshots(conn)
    mock_get.assert_not_called()
    assert result == {"matched": 0, "unmatched": 0, "skipped_errors": 0}


def test_poll_gmp_snapshots_aborts_cleanly_on_http_error(conn):
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    with patch(
        "ipo_watcher.gmp_source.fetch_gmp_listing",
        side_effect=requests.HTTPError("429 Client Error"),
    ):
        result = poll_gmp_snapshots(conn)
    assert result == {"matched": 0, "unmatched": 0, "skipped_errors": 0}
    assert conn.execute("SELECT COUNT(*) AS n FROM gmp_snapshots").fetchone()["n"] == 0
