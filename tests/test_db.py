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
    assert {"companies", "filings"} <= tables


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
