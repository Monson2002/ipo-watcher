# ipo_watcher/db.py
import sqlite3
import hashlib
import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES companies(id),
    filing_type       TEXT NOT NULL,
    filing_date       TEXT NOT NULL,
    document_url      TEXT NOT NULL,
    source_page_url   TEXT NOT NULL,
    dedup_key         TEXT NOT NULL UNIQUE,
    pdf_path          TEXT,
    pdf_sha256        TEXT,
    discovered_at     TEXT NOT NULL,
    notified_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_filings_company ON filings(company_id);
"""


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _now_iso():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compute_dedup_key(company_name, filing_type, filing_date, document_url):
    raw = f"{company_name}|{filing_type}|{filing_date}|{document_url}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_or_create_company(conn, name):
    row = conn.execute("SELECT id FROM companies WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"], False
    cur = conn.execute(
        "INSERT INTO companies (name, first_seen_at) VALUES (?, ?)",
        (name, _now_iso()),
    )
    conn.commit()
    return cur.lastrowid, True


def upsert_filing(conn, company_name, filing_type, filing_date, document_url, source_page_url):
    company_id, is_new_company = _get_or_create_company(conn, company_name)
    dedup_key = _compute_dedup_key(company_name, filing_type, filing_date, document_url)
    cur = conn.execute(
        """INSERT OR IGNORE INTO filings
           (company_id, filing_type, filing_date, document_url, source_page_url, dedup_key, discovered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (company_id, filing_type, filing_date, document_url, source_page_url, dedup_key, _now_iso()),
    )
    conn.commit()
    is_new_filing = cur.rowcount == 1
    return {
        "filing_id": cur.lastrowid if is_new_filing else None,
        "is_new_filing": is_new_filing,
        "is_new_company": is_new_company,
    }


def get_unnotified_filings(conn):
    return conn.execute(
        """SELECT f.*, c.name AS company_name,
                  (SELECT COUNT(*) FROM filings f2
                     WHERE f2.company_id = f.company_id AND f2.id < f.id) = 0 AS is_first_filing
           FROM filings f JOIN companies c ON c.id = f.company_id
           WHERE f.notified_at IS NULL
           ORDER BY f.id ASC"""
    ).fetchall()


def mark_notified(conn, filing_ids):
    if not filing_ids:
        return
    ts = _now_iso()
    conn.executemany(
        "UPDATE filings SET notified_at = ? WHERE id = ?",
        [(ts, fid) for fid in filing_ids],
    )
    conn.commit()


def get_filings_needing_download(conn):
    return conn.execute(
        """SELECT f.*, c.name AS company_name
           FROM filings f JOIN companies c ON c.id = f.company_id
           WHERE f.pdf_path IS NULL"""
    ).fetchall()


def set_pdf_info(conn, filing_id, pdf_path, pdf_sha256):
    conn.execute(
        "UPDATE filings SET pdf_path = ?, pdf_sha256 = ? WHERE id = ?",
        (pdf_path, pdf_sha256, filing_id),
    )
    conn.commit()
