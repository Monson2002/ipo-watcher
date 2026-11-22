# tests/test_db.py
import sqlite3
import pytest

from ipo_watcher import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_init_db_creates_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"companies", "filings", "gmp_snapshots", "gmp_notifications"} <= tables


def test_record_and_get_latest_gmp_pct(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4)
    assert db.get_latest_gmp_pct(conn, company_id) == 18.4


def test_get_latest_gmp_pct_returns_none_with_no_snapshot(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.get_latest_gmp_pct(conn, company_id) is None


def test_record_gmp_snapshot_gmp_rupees_defaults_to_null(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=5.0)
    row = conn.execute("SELECT gmp_rupees FROM gmp_snapshots WHERE company_id = ?", (company_id,)).fetchone()
    assert row["gmp_rupees"] is None


def test_get_latest_gmp_pct_handles_zero_and_negative_values(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=0.0)
    assert db.get_latest_gmp_pct(conn, company_id) == 0.0

    db.record_gmp_snapshot(conn, company_id, gmp_pct=-3.5)
    assert db.get_latest_gmp_pct(conn, company_id) == -3.5


def test_get_latest_gmp_pct_breaks_ties_by_insertion_order(conn, monkeypatch):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    monkeypatch.setattr(db, "_now_iso", lambda: "2026-10-11T10:00:00+05:30")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=10.0)
    db.record_gmp_snapshot(conn, company_id, gmp_pct=20.0)
    assert db.get_latest_gmp_pct(conn, company_id) == 20.0


def test_get_latest_gmp_pct_does_not_leak_between_companies(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    db.record_gmp_snapshot(conn, company_a, gmp_pct=10.0)
    db.record_gmp_snapshot(conn, company_b, gmp_pct=99.0)
    assert db.get_latest_gmp_pct(conn, company_a) == 10.0
    assert db.get_latest_gmp_pct(conn, company_b) == 99.0


def test_record_gmp_snapshot_stores_close_date_and_is_listed(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4, close_date="2026-10-20", is_listed=False)
    assert db.get_latest_close_date(conn, company_id) == "2026-10-20"
    assert db.get_latest_is_listed(conn, company_id) is False


def test_init_db_adds_open_date_column_to_pre_existing_gmp_snapshots_table():
    # Simulates the real production DB: a gmp_snapshots table created
    # before open_date existed in SCHEMA. CREATE TABLE IF NOT EXISTS is a
    # no-op against an existing table, so init_db must separately detect
    # and ALTER TABLE to add the missing column, or every poll crashes
    # with "table gmp_snapshots has no column named open_date".
    conn = db.connect(":memory:")
    conn.execute("""
        CREATE TABLE companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE gmp_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  INTEGER NOT NULL REFERENCES companies(id),
            gmp_rupees  REAL,
            gmp_pct     REAL NOT NULL,
            close_date  TEXT,
            is_listed   INTEGER,
            polled_at   TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO companies (name, first_seen_at) VALUES ('Acme Robotics Ltd', '2026-01-01T00:00:00+05:30')"
    )
    conn.execute(
        "INSERT INTO gmp_snapshots (company_id, gmp_pct, polled_at) VALUES (1, 5.0, '2026-01-01T00:00:00+05:30')"
    )
    conn.commit()

    db.init_db(conn)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(gmp_snapshots)")}
    assert "open_date" in cols
    row = conn.execute("SELECT gmp_pct, open_date FROM gmp_snapshots WHERE id = 1").fetchone()
    assert row["gmp_pct"] == 5.0
    assert row["open_date"] is None
    conn.close()


def test_init_db_is_idempotent_when_open_date_column_already_exists(conn):
    # Calling init_db twice (as every real run does, via db.connect + init_db
    # each time) must not raise "duplicate column name".
    db.init_db(conn)
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(gmp_snapshots)")}
    assert "open_date" in cols


def test_record_gmp_snapshot_stores_open_date(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4, open_date="2026-11-18")
    row = conn.execute(
        "SELECT open_date FROM gmp_snapshots WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert row["open_date"] == "2026-11-18"


def test_record_gmp_snapshot_open_date_defaults_to_null(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=5.0)
    row = conn.execute(
        "SELECT open_date FROM gmp_snapshots WHERE company_id = ?", (company_id,)
    ).fetchone()
    assert row["open_date"] is None


def test_has_notified_application_open_returns_false_with_no_prior_notification(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.has_notified_application_open(conn, company_id, "2026-11-18") is False


def test_has_notified_application_open_returns_true_once_recorded(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_application_open_notification(conn, company_id, "2026-11-18")
    assert db.has_notified_application_open(conn, company_id, "2026-11-18") is True


def test_has_notified_application_open_does_not_match_a_different_open_date(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_application_open_notification(conn, company_id, "2026-11-18")
    assert db.has_notified_application_open(conn, company_id, "2026-11-19") is False


def test_has_notified_application_open_does_not_leak_between_companies(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    db.record_application_open_notification(conn, company_a, "2026-11-18")
    assert db.has_notified_application_open(conn, company_b, "2026-11-18") is False


def test_has_notified_application_open_is_unaffected_by_unrelated_gmp_snapshots(conn):
    # Regression for the bug a review caught: recording a GMP snapshot
    # that happens to carry the same open_date value (days before the
    # window actually opens) must NOT look like an already-sent
    # notification. Only record_application_open_notification marks it sent.
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=4.0, open_date="2026-11-18")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=4.5, open_date="2026-11-18")
    assert db.has_notified_application_open(conn, company_id, "2026-11-18") is False


def test_record_application_open_notification_is_idempotent_per_company_and_open_date(conn):
    # Defense in depth, matching the filings.dedup_key pattern: a second
    # INSERT for the same (company_id, open_date) must not raise or create
    # a duplicate row, even though code-level dedup is also supposed to
    # prevent this from ever being attempted.
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_application_open_notification(conn, company_id, "2026-11-18")
    db.record_application_open_notification(conn, company_id, "2026-11-18")
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM application_open_notifications WHERE company_id = ? AND open_date = ?",
        (company_id, "2026-11-18"),
    ).fetchone()["n"]
    assert count == 1


def test_get_latest_close_date_returns_none_with_no_snapshot(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.get_latest_close_date(conn, company_id) is None


def test_get_latest_is_listed_returns_none_with_no_snapshot(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.get_latest_is_listed(conn, company_id) is None


def test_get_latest_is_listed_distinguishes_false_from_no_data(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=10.0, is_listed=False)
    assert db.get_latest_is_listed(conn, company_id) is False

    company_id_2, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    assert db.get_latest_is_listed(conn, company_id_2) is None


def test_get_latest_is_listed_picks_most_recent_value(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=10.0, is_listed=False)
    db.record_gmp_snapshot(conn, company_id, gmp_pct=10.0, is_listed=True)
    assert db.get_latest_is_listed(conn, company_id) is True


def test_record_and_get_last_notified_gmp_pct(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_notification(conn, company_id, gmp_pct=12.0)
    assert db.get_last_notified_gmp_pct(conn, company_id) == 12.0


def test_get_last_notified_gmp_pct_returns_none_with_no_notification(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.get_last_notified_gmp_pct(conn, company_id) is None


def test_get_last_notified_gmp_pct_picks_most_recent(conn, monkeypatch):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    monkeypatch.setattr(db, "_now_iso", lambda: "2026-10-11T10:00:00+05:30")
    db.record_gmp_notification(conn, company_id, gmp_pct=10.0)
    db.record_gmp_notification(conn, company_id, gmp_pct=18.0)
    assert db.get_last_notified_gmp_pct(conn, company_id) == 18.0


def test_get_notified_company_ids_returns_distinct_companies_with_history(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    db._get_or_create_company(conn, "Never Notified Co")
    db.record_gmp_notification(conn, company_a, gmp_pct=10.0)
    db.record_gmp_notification(conn, company_a, gmp_pct=15.0)
    db.record_gmp_notification(conn, company_b, gmp_pct=20.0)
    assert sorted(db.get_notified_company_ids(conn)) == sorted([company_a, company_b])


def test_record_gmp_snapshot_rejects_unknown_company_id(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.record_gmp_snapshot(conn, company_id=999999, gmp_pct=10.0)


def test_upsert_filing_creates_company_on_first_filing(conn):
    result = db.upsert_filing(
        conn,
        company_name="Gaurik Fashions Limited",
        filing_type="Addendum to DRHP",
        filing_date="2026-09-04",
        document_url="https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.PDF",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/gaurik-fashions-limited-addendum-to-drhp_104276.html",
    )
    assert result["is_new_filing"] is True
    assert result["is_new_company"] is True
    company_row = conn.execute(
        "SELECT * FROM companies WHERE name = ?", ("Gaurik Fashions Limited",)
    ).fetchone()
    assert company_row is not None


def test_upsert_filing_is_idempotent(conn):
    args = dict(
        company_name="Acme Robotics Ltd",
        filing_type="DRHP",
        filing_date="2026-09-05",
        document_url="https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/2.PDF",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/acme-robotics-drhp_1.html",
    )
    first = db.upsert_filing(conn, **args)
    second = db.upsert_filing(conn, **args)
    assert first["is_new_filing"] is True
    assert second["is_new_filing"] is False
    count = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    assert count == 1


def test_corrigendum_reuses_existing_company(conn):
    db.upsert_filing(
        conn,
        company_name="Acme Robotics Ltd",
        filing_type="DRHP",
        filing_date="2026-09-05",
        document_url="https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/2.PDF",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/acme-robotics-drhp_1.html",
    )
    result = db.upsert_filing(
        conn,
        company_name="Acme Robotics Ltd",
        filing_type="Corrigendum to DRHP",
        filing_date="2026-09-06",
        document_url="https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/3.PDF",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/acme-robotics-corrigendum_2.html",
    )
    assert result["is_new_filing"] is True
    assert result["is_new_company"] is False
    count = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    assert count == 2


def test_get_unnotified_filings_marks_first_filing(conn):
    db.upsert_filing(
        conn, company_name="New Co", filing_type="DRHP", filing_date="2026-09-01",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    db.upsert_filing(
        conn, company_name="New Co", filing_type="Corrigendum to DRHP", filing_date="2026-09-02",
        document_url="https://x/2.pdf", source_page_url="https://x/2.html",
    )
    rows = db.get_unnotified_filings(conn)
    assert len(rows) == 2
    assert rows[0]["is_first_filing"] == 1
    assert rows[1]["is_first_filing"] == 0
    assert rows[0]["company_name"] == "New Co"

    db.mark_notified(conn, [r["id"] for r in rows])
    assert db.get_unnotified_filings(conn) == []


def test_get_filings_needing_download_and_set_pdf_info(conn):
    result = db.upsert_filing(
        conn, company_name="New Co", filing_type="DRHP", filing_date="2026-09-01",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    pending = db.get_filings_needing_download(conn)
    assert len(pending) == 1
    assert pending[0]["company_name"] == "New Co"

    db.set_pdf_info(conn, result["filing_id"], "data/pdfs/new_co/1.pdf", "deadbeef")
    assert db.get_filings_needing_download(conn) == []


def test_get_filing_notified_company_ids_includes_companies_notified_with_no_gmp_baseline(conn):
    company_id, _ = db._get_or_create_company(conn, "New Co")
    result = db.upsert_filing(
        conn, company_name="New Co", filing_type="DRHP", filing_date="2026-09-01",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    assert db.get_filing_notified_company_ids(conn) == []
    db.mark_notified(conn, [result["filing_id"]])
    assert db.get_filing_notified_company_ids(conn) == [company_id]


def test_get_filing_notified_company_ids_excludes_unnotified_filings(conn):
    db.upsert_filing(
        conn, company_name="New Co", filing_type="DRHP", filing_date="2026-09-01",
        document_url="https://x/1.pdf", source_page_url="https://x/1.html",
    )
    assert db.get_filing_notified_company_ids(conn) == []
