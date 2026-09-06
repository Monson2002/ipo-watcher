# SEBI IPO Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted Python tool that periodically discovers new SEBI public-issue filings (DRHP/RHP/Prospectus/Addenda/Corrigenda), downloads each filing's PDF, and emails the user a batched alert when something new appears.

**Architecture:** A single flat `ipo_watcher` package with one file per concern (fetch/parse SEBI pages, SQLite persistence, PDF download, SMTP email, orchestration). No web framework, no ORM, no cloud services — a plain script invoked by cron, storing state in one SQLite file and PDFs on local disk.

**Tech Stack:** Python 3, `requests`, `beautifulsoup4` is NOT used (regex-based parsing turned out to be more robust than DOM parsing against SEBI's malformed nested-anchor markup — see Task 2), `python-dotenv`, stdlib `sqlite3`/`smtplib`/`logging`/`unittest.mock`.

**Spec:** `/home/monson2002/.claude/plans/not-as-a-normal-merry-diffie.md` (the approved design doc — scope, storage choice, and email-alert requirement were decided there; this plan supersedes that doc's SEBI-access details with facts confirmed by live inspection below).

## Global Constraints

- Python 3, no cloud provider, no IaC — self-hosted script + cron only.
- Storage: SQLite file (`data/ipo_watcher.db`) + local disk (`data/pdfs/`). No DB server process.
- Alerts: email via stdlib `smtplib` only. No third-party mail library.
- Dependencies limited to: `requests`, `python-dotenv`. No `beautifulsoup4` (see Task 2 rationale), no `playwright` (SEBI's listing is confirmed server-rendered HTML, no JS execution needed), no ORM, no retry/backoff library (a manual retry loop suffices), no `pydantic`/`click`/`typer`.
- Package management: `uv` + `pyproject.toml`, not `pip`/`requirements.txt`. Install with `uv sync`, run anything with `uv run <cmd>` (e.g. `uv run pytest`, `uv run python -m ipo_watcher.run`). Every `pytest ...`/`python ...` invocation elsewhere in this plan means "inside the project's `uv`-managed environment."
- Out of scope entirely for this plan: PDF financial-metric extraction, IPO scoring/recommendation, GMP/market-price data. Only filing discovery + PDF download + email alert.
- No automated test may hit the live SEBI site — all `sebi_source.py` tests run against embedded fixture strings captured from real pages (see Task 2-4).
- Tests use stdlib `unittest`/`pytest` + `unittest.mock` only — no `requests_mock`/`responses` dependency.

---

## Confirmed facts about SEBI's site (from live inspection — do not re-derive these)

**Listing endpoint (page 1):**
`GET https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&ssid=15&smid=-1`
- `sid=3&ssid=15` = "Filings" → "Public Issues". `smid=-1` = **all sub-sections combined** (DRHP, RHP, Prospectus, Addendum, Addendum II, Corrigendum, etc. all in one listing) — confirmed by a live request; using a single `smid` value like `10` returns only one filing sub-type and would silently miss RHPs/Prospectuses/Addenda.
- Server-rendered HTML, no JSON API, no JS execution required. Table `id='sample_1'`, 2 columns: Date, Title.
- Each row: `<tr...><td>Sep 04, 2026</td><td><a href="https://www.sebi.gov.in/filings/public-issues/{mon-yyyy}/{slug}_{id}.html" target="_blank" title="...">{visible text}</a>...</td></tr>`.
- `{visible text}` is usually `"{Company Name} - {Filing Type}"` (e.g. `"Gaurik Fashions Limited - Addendum to DRHP"`), but sometimes just `"{Company Name}"` with no type suffix (e.g. `"India Exposition Mart Limited"`).
- Some rows additionally embed a **nested, invalid `<a>` inside the outer `<a>`** (e.g. Tablespace Technologies' row links to both its DRHP detail page *and*, nested inside, a direct PDF link to its Abridged Prospectus). This nested markup breaks naive tag-based scraping — handled explicitly in Task 2.

**Pagination (page ≥ 2), confirmed live, twice:**
`POST https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp`
Form-urlencoded body required fields: `nextValue`, `next`, `search`, `fromDate`, `toDate`, `fromYear`, `toYear`, `deptId`, `sid`, `ssid`, `smid`, `ssidhidden`, `intmid`, `sText`, `ssText`, `smText`, `doDirect`.
For page `N` (`N >= 2`): `nextValue = str(N-1)`, `doDirect = str(N-1)`, `next = "n"`. Verified: this fetched "26 to 50 of 2193 records" (page 2) and "51 to 75 of 2195 records" (page 3).
**No session cookie or CSRF token required** — verified the page-3 request with zero cookies and it worked identically. Response body is plain text, `"#@#"`-delimited; the part before the delimiter is an HTML fragment containing the same `<tr>` row markup as page 1, safe to run through the same row parser.

**Filing detail page → real PDF URL:**
Each `.html` detail page embeds the actual DRHP/RHP as an iframe-based viewer:
`<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/{mon-yyyy}/{numeric_id}.PDF' ...>`
Confirmed on two real filings:
- `.../jun-2026/jio-platforms-ltd-drhp_102248.html` → `https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1781870482419.PDF`
- `.../sep-2026/lcc-projects-limited-rhp_104283.html` → `https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1788524729720.pdf`

The detail page also separately links a shorter "Abridged Prospectus" PDF under `sebi_data/commondocs/...` (in the page's malformed `<title>`/`<h1>` markup, and sometimes in the listing row itself). **This plan deliberately ignores the abridged prospectus** and stores only the full DRHP/RHP from the iframe as `document_url` — the abridged version is a summary, not useful for the future financial-extraction component this collector feeds. Don't add a second document column/table for it; it's out of scope until something actually needs it.

**Why regex instead of BeautifulSoup:** the malformed nested `<a>` inside `<a>` in listing rows, and the raw unescaped `<br>`/`<a>` markup stuffed into `title=`/`<h1>` attributes on detail pages, make a real DOM parser fight the markup instead of helping. A narrow regex anchored on the one well-formed structure that matters (the outer row's `href`+leading text; the iframe's `src`) is simpler and more robust here — this is the concrete case where "stdlib does it" wins over reaching for a parsing library.

---

## File Structure

```
ipo-watcher/
├── .env.example
├── .gitignore
├── pyproject.toml
├── uv.lock
├── README.md
├── ipo_watcher/
│   ├── __init__.py
│   ├── config.py          # env var reads
│   ├── db.py               # sqlite schema + query helpers
│   ├── sebi_source.py       # fetch/parse SEBI listing + detail pages
│   ├── downloader.py         # PDF download, naming, hashing
│   ├── notifier.py            # email body building + SMTP send
│   └── run.py                  # orchestration entrypoint
├── data/                      # created at runtime: ipo_watcher.db, pdfs/
├── logs/                      # created at runtime: ipo_watcher.log
└── tests/
    ├── test_db.py
    ├── test_sebi_source.py
    ├── test_downloader.py
    ├── test_notifier.py
    └── test_run.py
```

---

### Task 1: Project scaffold + SQLite persistence (`db.py`)

**Files:**
- Create: `pyproject.toml`, `.env.example`, `.gitignore`
- Create: `ipo_watcher/__init__.py` (empty), `ipo_watcher/config.py`, `ipo_watcher/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.connect(db_path) -> sqlite3.Connection`, `db.init_db(conn)`, `db.upsert_filing(conn, company_name, filing_type, filing_date, document_url, source_page_url) -> dict` (keys: `filing_id`, `is_new_filing`, `is_new_company`), `db.get_unnotified_filings(conn) -> list[sqlite3.Row]` (each row has `company_name`, `filing_type`, `filing_date`, `document_url`, `source_page_url`, `is_first_filing`), `db.mark_notified(conn, filing_ids: list[int])`, `db.get_filings_needing_download(conn) -> list[sqlite3.Row]` (each row has `company_name`, `filing_date`, `filing_type`, `dedup_key`, `document_url`, `id`), `db.set_pdf_info(conn, filing_id, pdf_path, pdf_sha256)`.
- Consumes: nothing (this is the base layer).

- [ ] **Step 1: Scaffold the repo**

Use `uv` to create `pyproject.toml` and add dependencies (this also creates `.venv/` and `uv.lock`):

```bash
uv init --no-readme --name ipo-watcher --python 3.12
uv add requests==2.32.3 python-dotenv==1.0.1
uv add --dev pytest
```

(If `uv init` scaffolds its own placeholder `main.py`/`README.md`, delete them — this project's entrypoint is `ipo_watcher/run.py`, added in Task 7.)

```
.env.example:
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
ALERT_FROM=you@gmail.com
ALERT_TO=you@gmail.com
```

```
.gitignore:
.env
data/
logs/
__pycache__/
*.pyc
.venv/
```

Create `ipo_watcher/__init__.py` as an empty file.

- [ ] **Step 2: Write `config.py`**

```python
# ipo_watcher/config.py
import os
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
ALERT_FROM = os.environ.get("ALERT_FROM", SMTP_USER)
ALERT_TO = os.environ.get("ALERT_TO", "")

DB_PATH = os.environ.get("IPO_WATCHER_DB_PATH", "data/ipo_watcher.db")
PDF_DIR = os.environ.get("IPO_WATCHER_PDF_DIR", "data/pdfs")
LOG_PATH = os.environ.get("IPO_WATCHER_LOG_PATH", "logs/ipo_watcher.log")
```

- [ ] **Step 3: Write the failing test for schema + company creation**

```python
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
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError` or `AttributeError` (no `db.py` / no `connect`/`init_db`/`upsert_filing` yet).

- [ ] **Step 5: Implement schema + connect/init_db/upsert_filing**

```python
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
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Write the failing test for dedup + corrigendum-on-known-company**

```python
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
```

- [ ] **Step 8: Run test to verify it fails is not needed** — these exercise the same `upsert_filing` from Step 5, so instead run directly:

Run: `pytest tests/test_db.py -v`
Expected: PASS (4 tests total). If any fail, the dedup key or `INSERT OR IGNORE` logic in Step 5 has a bug — fix `_compute_dedup_key`/the SQL before continuing.

- [ ] **Step 9: Write the failing test for notify/download query helpers**

```python
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
```

- [ ] **Step 10: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `AttributeError` (`get_unnotified_filings`/`mark_notified`/`get_filings_needing_download`/`set_pdf_info` don't exist yet).

- [ ] **Step 11: Implement the remaining query helpers**

Append to `ipo_watcher/db.py`:

```python
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
```

- [ ] **Step 12: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 13: Commit**

```bash
git add pyproject.toml uv.lock .env.example .gitignore ipo_watcher/__init__.py ipo_watcher/config.py ipo_watcher/db.py tests/test_db.py
git commit -m "feat: scaffold project + SQLite persistence layer"
```

---

### Task 2: SEBI listing fetch + row parser (`sebi_source.py`, page 1)

**Files:**
- Create: `ipo_watcher/sebi_source.py`
- Test: `tests/test_sebi_source.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `sebi_source.FilingRow` (dataclass: `company_name`, `filing_type`, `filing_date` [ISO `YYYY-MM-DD`], `source_page_url`), `sebi_source.parse_listing(html: str) -> list[FilingRow]`, `sebi_source.fetch_listing_page(page_no: int = 1) -> str`.

- [ ] **Step 1: Write the failing test for `parse_listing` against a real fixture**

This fixture is a trimmed real excerpt of `HomeAction.do`'s response — includes a plain row, a row with no filing-type suffix, and a row with the malformed nested `<a>` (the exact case that breaks naive HTML parsing), copied verbatim from a live fetch.

```python
# tests/test_sebi_source.py
from ipo_watcher.sebi_source import parse_listing, FilingRow

LISTING_FIXTURE = """
<table id='sample_1'>
<thead><tr role='row'><th style='width:18%'>Date</th><th>Title</th></tr></thead>
<tbody>
<tr role='row' class='odd'>
    <td>Sep 04, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/sep-2026/gaurik-fashions-limited-addendum-to-drhp_104276.html"  target="_blank" title="Gaurik Fashions Limited - Addendum to DRHP" class="points"> Gaurik Fashions Limited - Addendum to DRHP</a>
</tr>
<tr role='row' class='even'>
    <td>Sep 04, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/sep-2026/india-exposition-mart-limited_104273.html"  target="_blank" title="India Exposition Mart Limited" class="points"> India Exposition Mart Limited</a>
</tr>
<tr role='row' class='odd'>
    <td>Aug 14, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/aug-2026/tablespace-technologies-limited-drhp_103710.html"  target="_blank" title="TABLESPACE TECHNOLOGIES LIMITED - DRHP&lt;br&gt;..." class="points"> TABLESPACE TECHNOLOGIES LIMITED - DRHP<br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/aug-2026/TABLESPACE%20TECHNOLOGIES%20LIMITED%20-%20DAP_p.pdf' style=color:#000000>TABLESPACE TECHNOLOGIES LIMITED - Draft Abridged Prospectus</a></a>
</tr>
</tbody>
</table>
"""


def test_parse_listing_plain_row_with_type_suffix():
    rows = parse_listing(LISTING_FIXTURE)
    assert rows[0] == FilingRow(
        company_name="Gaurik Fashions Limited",
        filing_type="Addendum to DRHP",
        filing_date="2026-09-04",
        source_page_url="https://www.sebi.gov.in/filings/public-issues/sep-2026/gaurik-fashions-limited-addendum-to-drhp_104276.html",
    )


def test_parse_listing_row_with_no_type_suffix():
    rows = parse_listing(LISTING_FIXTURE)
    assert rows[1].company_name == "India Exposition Mart Limited"
    assert rows[1].filing_type == "Unspecified"


def test_parse_listing_row_with_nested_anchor_only_captures_outer_row():
    rows = parse_listing(LISTING_FIXTURE)
    assert len(rows) == 3
    assert rows[2].company_name == "TABLESPACE TECHNOLOGIES LIMITED"
    assert rows[2].filing_type == "DRHP"
    assert rows[2].source_page_url.endswith("tablespace-technologies-limited-drhp_103710.html")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sebi_source.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `sebi_source.py` yet).

- [ ] **Step 3: Implement `FilingRow` and `parse_listing`**

```python
# ipo_watcher/sebi_source.py
import re
import time
import datetime
from dataclasses import dataclass

import requests

BASE = "https://www.sebi.gov.in"
LISTING_URL = f"{BASE}/sebiweb/home/HomeAction.do"
PAGINATE_URL = f"{BASE}/sebiweb/ajax/home/getnewslistinfo.jsp"
# Public Issues, all filing sub-types combined (DRHP/RHP/Prospectus/Addenda/...)
SECTION_PARAMS = {"sid": "3", "ssid": "15", "smid": "-1"}

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3


@dataclass
class FilingRow:
    company_name: str
    filing_type: str
    filing_date: str  # ISO YYYY-MM-DD
    source_page_url: str


ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(?P<date>[^<]+)</td>\s*<td[^>]*>\s*"
    r"<a\s+href=\"(?P<url>https://www\.sebi\.gov\.in/filings/public-issues/[^\"]+\.html)\"[^>]*>"
    r"(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_company_and_type(visible_text):
    text = visible_text.strip()
    if " - " in text:
        company_name, filing_type = text.rsplit(" - ", 1)
        return company_name.strip(), filing_type.strip()
    return text, "Unspecified"


def parse_listing(html):
    """Parse a listing page (or AJAX pagination fragment) into FilingRows.
    Handles rows where the title cell contains a nested, invalid <a> (e.g. a
    directly-linked Abridged Prospectus) by only taking the text before the
    first '<' — this discards the nested markup regardless of where the
    regex match itself terminated."""
    rows = []
    for m in ROW_RE.finditer(html):
        date_text = m.group("date").strip()
        try:
            filing_date = datetime.datetime.strptime(date_text, "%b %d, %Y").date().isoformat()
        except ValueError:
            continue
        visible_text = re.split(r"<", m.group("text"), maxsplit=1)[0]
        company_name, filing_type = _parse_company_and_type(visible_text)
        if not company_name:
            continue
        rows.append(FilingRow(
            company_name=company_name,
            filing_type=filing_type,
            filing_date=filing_date,
            source_page_url=m.group("url"),
        ))
    return rows


def _get_with_retries(url, params=None):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_exc


def fetch_listing_page(page_no=1):
    """Fetch raw HTML for one 1-indexed page of SEBI's Public Issues listing."""
    return _get_with_retries(LISTING_URL, params={"doListing": "yes", **SECTION_PARAMS})
```

(`fetch_listing_page` only handles `page_no == 1` for now — pagination is added in Task 3.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sebi_source.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/sebi_source.py tests/test_sebi_source.py
git commit -m "feat: parse SEBI public-issues listing rows"
```

---

### Task 3: Pagination via SEBI's AJAX endpoint

**Files:**
- Modify: `ipo_watcher/sebi_source.py`
- Test: `tests/test_sebi_source.py`

**Interfaces:**
- Consumes: `ROW_RE`, `_get_with_retries` pattern from Task 2 (mirrored for POST).
- Produces: `sebi_source.fetch_listing_page(page_no)` now handles `page_no >= 2` too, via a new `_post_with_retries` helper.

- [ ] **Step 1: Write the failing test for the pagination fragment**

This fixture is a trimmed real excerpt of the AJAX response body (confirmed live: requesting page 2 returns "26 to 50 of 2193 records" and a `#@#`-delimited breadcrumb section after the main fragment).

```python
# append to tests/test_sebi_source.py
from unittest.mock import patch, MagicMock

AJAX_FRAGMENT = """<input type='hidden' name='totalpage' value=88 /><input type='hidden' name='nextDel' value='25'/><input type='hidden' name='nextValue' value='2'/>
<table id='sample_1'><tbody>
<tr role='row' class='odd'>
    <td>Aug 06, 2026</td>
    <td><a href="https://www.sebi.gov.in/filings/public-issues/aug-2026/encube-ethicals-limited-drhp_103383.html"  target="_blank" title="Encube Ethicals Limited - DRHP" class="points"> Encube Ethicals Limited - DRHP</a>
</tr>
</tbody></table>
#@#<ol class='cfc-breadcrumb'></ol>"""


def test_fetch_listing_page_2_posts_to_ajax_endpoint_and_strips_delimiter():
    from ipo_watcher import sebi_source

    fake_response = MagicMock()
    fake_response.text = AJAX_FRAGMENT
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.sebi_source.requests.post", return_value=fake_response) as mock_post:
        html = sebi_source.fetch_listing_page(page_no=2)

    assert "#@#" not in html
    rows = sebi_source.parse_listing(html)
    assert rows[0].company_name == "Encube Ethicals Limited"

    _, kwargs = mock_post.call_args
    assert kwargs["data"]["nextValue"] == "1"
    assert kwargs["data"]["doDirect"] == "1"
    assert kwargs["data"]["next"] == "n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sebi_source.py -v`
Expected: FAIL — `fetch_listing_page(page_no=2)` currently ignores `page_no` and always GETs page 1, so `requests.post` is never called and the mock assertion fails.

- [ ] **Step 3: Implement pagination**

Replace `fetch_listing_page` in `ipo_watcher/sebi_source.py` with:

```python
def _post_with_retries(url, data):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                data=data,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_exc


def fetch_listing_page(page_no=1):
    """Fetch raw HTML for one 1-indexed page of SEBI's Public Issues listing."""
    if page_no == 1:
        return _get_with_retries(LISTING_URL, params={"doListing": "yes", **SECTION_PARAMS})
    n = str(page_no - 1)
    data = {
        "nextValue": n,
        "next": "n",
        "search": "",
        "fromDate": "",
        "toDate": "",
        "fromYear": "",
        "toYear": "",
        "deptId": "",
        "sid": SECTION_PARAMS["sid"],
        "ssid": SECTION_PARAMS["ssid"],
        "smid": SECTION_PARAMS["smid"],
        "ssidhidden": SECTION_PARAMS["ssid"],
        "intmid": "-1",
        "sText": "Filings",
        "ssText": "Public Issues",
        "smText": "",
        "doDirect": n,
    }
    raw = _post_with_retries(PAGINATE_URL, data)
    return raw.split("#@#")[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sebi_source.py -v`
Expected: PASS (4 tests total).

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/sebi_source.py tests/test_sebi_source.py
git commit -m "feat: paginate SEBI listing via its AJAX endpoint"
```

---

### Task 4: Filing detail page → real PDF URL (`parse_detail`)

**Files:**
- Modify: `ipo_watcher/sebi_source.py`
- Test: `tests/test_sebi_source.py`

**Interfaces:**
- Produces: `sebi_source.fetch_detail_page(source_page_url: str) -> str`, `sebi_source.parse_detail(html: str) -> str | None`.

- [ ] **Step 1: Write the failing test using real detail-page excerpts**

These two fixtures are the exact iframe lines confirmed live on Jio Platforms' DRHP page and LCC Projects' RHP page (surrounding malformed `<title>`/`<h1>` markup omitted — `parse_detail` must not depend on it).

```python
# append to tests/test_sebi_source.py
JIO_DETAIL_FIXTURE = """
<h1>Jio Platforms Ltd - DRHP<br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/jun-2026/Jio%20Platforms%20Ltd_p.PDF' style=color:#000000>Jio Platforms Ltd - Abridged Prospectus</a></h1>
<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1781870482419.PDF' width='100%' style='max-height:90%; height:600px;' allowfullscreen webkitallowfullscreen></iframe>
"""

LCC_DETAIL_FIXTURE = """
<h1>LCC PROJECTS LIMITED - RHP <br><a href= 'https://www.sebi.gov.in/sebi_data/commondocs/sep-2026/LCC%20PROJECTS%20LIMITED%20-%20AP_p.pdf' style=color:#000000>LCC PROJECTS LIMITED - Abridged Prospectus</a></h1>
<iframe src='../../../web/?file=https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1788524729720.pdf' width='100%' style='max-height:90%; height:600px;' allowfullscreen webkitallowfullscreen></iframe>
"""


def test_parse_detail_extracts_iframe_pdf_not_abridged_prospectus():
    from ipo_watcher.sebi_source import parse_detail

    assert parse_detail(JIO_DETAIL_FIXTURE) == (
        "https://www.sebi.gov.in/sebi_data/attachdocs/jun-2026/1781870482419.PDF"
    )
    assert parse_detail(LCC_DETAIL_FIXTURE) == (
        "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1788524729720.pdf"
    )


def test_parse_detail_returns_none_when_no_iframe():
    from ipo_watcher.sebi_source import parse_detail

    assert parse_detail("<html><body>no viewer here</body></html>") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sebi_source.py -v`
Expected: FAIL with `ImportError` (`parse_detail` doesn't exist yet).

- [ ] **Step 3: Implement `fetch_detail_page` and `parse_detail`**

Append to `ipo_watcher/sebi_source.py`:

```python
DOC_IFRAME_RE = re.compile(
    r"<iframe[^>]+src=['\"]\.\./\.\./\.\./web/\?file=(?P<url>[^'\"]+)['\"]",
    re.IGNORECASE,
)


def fetch_detail_page(source_page_url):
    return _get_with_retries(source_page_url)


def parse_detail(html):
    """Extract the full DRHP/RHP PDF URL embedded via the detail page's
    iframe viewer. Deliberately ignores the separate Abridged Prospectus
    link that also appears on this page (see plan doc: out of scope).
    Returns None if no iframe is found."""
    m = DOC_IFRAME_RE.search(html)
    return m.group("url") if m else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sebi_source.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/sebi_source.py tests/test_sebi_source.py
git commit -m "feat: extract real DRHP/RHP PDF url from filing detail pages"
```

---

### Task 5: PDF downloader (`downloader.py`)

**Files:**
- Create: `ipo_watcher/downloader.py`
- Test: `tests/test_downloader.py`

**Interfaces:**
- Consumes: nothing from other `ipo_watcher` modules.
- Produces: `downloader.slugify(text: str) -> str`, `downloader.build_pdf_path(pdf_dir, company_name, filing_date, filing_type, dedup_key) -> str`, `downloader.download_pdf(document_url: str, dest_path: str) -> str` (returns sha256 hex digest; raises `requests.RequestException` after retries exhausted).

- [ ] **Step 1: Write the failing test for naming**

```python
# tests/test_downloader.py
import os
from ipo_watcher import downloader


def test_slugify():
    assert downloader.slugify("Gaurik Fashions Limited") == "gaurik_fashions_limited"
    assert downloader.slugify("Addendum to DRHP") == "addendum_to_drhp"


def test_build_pdf_path():
    path = downloader.build_pdf_path(
        "data/pdfs", "Gaurik Fashions Limited", "2026-09-04", "Addendum to DRHP", "abcd1234ef567890"
    )
    assert path == os.path.join(
        "data/pdfs", "gaurik_fashions_limited", "2026-09-04_addendum_to_drhp_abcd1234.pdf"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_downloader.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `slugify` and `build_pdf_path`**

```python
# ipo_watcher/downloader.py
import hashlib
import os
import re
import time

import requests

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 60
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def build_pdf_path(pdf_dir, company_name, filing_date, filing_type, dedup_key):
    company_slug = slugify(company_name)
    type_slug = slugify(filing_type)
    filename = f"{filing_date}_{type_slug}_{dedup_key[:8]}.pdf"
    return os.path.join(pdf_dir, company_slug, filename)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_downloader.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing test for `download_pdf`**

```python
# append to tests/test_downloader.py
from unittest.mock import patch, MagicMock
import tempfile


def test_download_pdf_writes_file_and_returns_sha256(tmp_path):
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.iter_content = MagicMock(return_value=[b"PDF-BYTES-HERE"])
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    dest = str(tmp_path / "co" / "file.pdf")
    with patch("ipo_watcher.downloader.requests.get", return_value=fake_resp) as mock_get:
        digest = downloader.download_pdf("https://example.com/a.pdf", dest)

    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"PDF-BYTES-HERE"
    expected_digest = hashlib.sha256(b"PDF-BYTES-HERE").hexdigest()
    assert digest == expected_digest
    assert not os.path.exists(dest + ".tmp")
    mock_get.assert_called_once()
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_downloader.py -v`
Expected: FAIL with `AttributeError` (`download_pdf` doesn't exist yet).

- [ ] **Step 7: Implement `download_pdf`**

Append to `ipo_watcher/downloader.py`:

```python
def download_pdf(document_url, dest_path):
    """Download document_url to dest_path via a temp file + atomic rename.
    Returns the sha256 hex digest of the downloaded bytes."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + ".tmp"
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            digest = hashlib.sha256()
            with requests.get(
                document_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True
            ) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            digest.update(chunk)
            os.replace(tmp_path, dest_path)
            return digest.hexdigest()
        except requests.RequestException as exc:
            last_exc = exc
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_exc
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_downloader.py -v`
Expected: PASS (3 tests total).

- [ ] **Step 9: Commit**

```bash
git add ipo_watcher/downloader.py tests/test_downloader.py
git commit -m "feat: download filing PDFs with atomic writes and sha256 hashing"
```

---

### Task 6: Email alert (`notifier.py`)

**Files:**
- Create: `ipo_watcher/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `config.SMTP_HOST`, `config.SMTP_PORT`, `config.SMTP_USER`, `config.SMTP_PASSWORD`, `config.ALERT_FROM`, `config.ALERT_TO` (from Task 1). Rows are dict-like objects (sqlite3.Row from `db.get_unnotified_filings`, or plain dicts in tests) with keys `company_name`, `filing_type`, `filing_date`, `document_url`, `source_page_url`, `is_first_filing`.
- Produces: `notifier.build_email_subject(rows) -> str`, `notifier.build_email_body(rows) -> str`, `notifier.send_alert(rows) -> bool` (True on success).

- [ ] **Step 1: Write the failing test for body/subject building**

```python
# tests/test_notifier.py
from ipo_watcher import notifier

ROWS = [
    {
        "company_name": "Example Textiles Ltd",
        "filing_type": "DRHP",
        "filing_date": "2026-09-05",
        "document_url": "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/1.pdf",
        "source_page_url": "https://www.sebi.gov.in/filings/public-issues/sep-2026/example-textiles-drhp_1.html",
        "is_first_filing": 1,
    },
    {
        "company_name": "Acme Robotics Ltd",
        "filing_type": "Corrigendum to DRHP",
        "filing_date": "2026-09-05",
        "document_url": "https://www.sebi.gov.in/sebi_data/attachdocs/sep-2026/2.pdf",
        "source_page_url": "https://www.sebi.gov.in/filings/public-issues/sep-2026/acme-corrigendum_2.html",
        "is_first_filing": 0,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement body/subject builders**

```python
# ipo_watcher/notifier.py
import smtplib
from email.message import EmailMessage

from . import config


def build_email_subject(rows):
    count = len(rows)
    plural = "" if count == 1 else "s"
    return f"IPO Watcher: {count} new SEBI filing{plural}"


def build_email_body(rows):
    lines = []
    for r in rows:
        header = "New company" if r["is_first_filing"] else "Existing company"
        lines.append(f"{header}: {r['company_name']}")
        lines.append(f"  {r['filing_type']} filed {r['filing_date']}")
        lines.append(f"  {r['document_url']}")
        lines.append(f"  (source: {r['source_page_url']})")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Write the failing test for `send_alert`**

```python
# append to tests/test_notifier.py
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_notifier.py -v`
Expected: FAIL with `AttributeError` (`send_alert` doesn't exist yet).

- [ ] **Step 7: Implement `send_alert`**

Append to `ipo_watcher/notifier.py`:

```python
def send_alert(rows):
    """Sends one batched email for all rows. Returns True on success, False
    on any SMTP/network failure — caller decides whether to retry (by not
    marking these filings as notified)."""
    if not rows:
        return True
    msg = EmailMessage()
    msg["Subject"] = build_email_subject(rows)
    msg["From"] = config.ALERT_FROM
    msg["To"] = config.ALERT_TO
    msg.set_content(build_email_body(rows))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_notifier.py -v`
Expected: PASS (5 tests total).

- [ ] **Step 9: Commit**

```bash
git add ipo_watcher/notifier.py tests/test_notifier.py
git commit -m "feat: build and send batched email alerts for new filings"
```

---

### Task 7: Orchestration (`run.py`)

**Files:**
- Create: `ipo_watcher/run.py`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.upsert_filing`, `db.get_unnotified_filings`, `db.mark_notified`, `db.get_filings_needing_download`, `db.set_pdf_info` (Task 1); `sebi_source.fetch_listing_page`, `sebi_source.parse_listing`, `sebi_source.fetch_detail_page`, `sebi_source.parse_detail` (Tasks 2-4); `downloader.build_pdf_path`, `downloader.download_pdf` (Task 5); `notifier.send_alert` (Task 6); `config.DB_PATH`, `config.PDF_DIR`, `config.LOG_PATH` (Task 1).
- Produces: `run.discover_new_filings(conn)`, `run.download_pending_pdfs(conn)`, `run.send_pending_alert(conn)`, `run.main()` (entrypoint, calls all three then closes the connection).

- [ ] **Step 1: Write the failing end-to-end test**

This test mocks only the network boundary (`sebi_source.fetch_listing_page`/`fetch_detail_page` and `downloader.download_pdf`/`notifier.send_alert`) and exercises real SQLite (a temp file) — proving the wiring, not re-testing each module's internals.

```python
# tests/test_run.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `run.py` yet).

- [ ] **Step 3: Implement `run.py`**

```python
# ipo_watcher/run.py
import logging
import logging.handlers
import os

from . import config, db, sebi_source, downloader, notifier

MAX_PAGES = 20  # safety cap; normal runs stop once a page yields nothing new
log = logging.getLogger("ipo_watcher.run")


def setup_logging():
    os.makedirs(os.path.dirname(config.LOG_PATH), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
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
            break
        any_new = False
        for row in rows:
            try:
                document_url = _resolve_document_url(row)
            except Exception:
                log.exception("Failed to fetch/parse detail page %s", row.source_page_url)
                continue
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
        if not any_new:
            break
        page_no += 1


def download_pending_pdfs(conn):
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
        db.set_pdf_info(conn, filing["id"], dest_path, sha256)


def send_pending_alert(conn):
    rows = db.get_unnotified_filings(conn)
    if not rows:
        return
    ok = notifier.send_alert(rows)
    if ok:
        db.mark_notified(conn, [r["id"] for r in rows])
    else:
        log.error("Failed to send alert email for %d filing(s); will retry next run", len(rows))


def main():
    setup_logging()
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS (all tests across every task — 22 total).

- [ ] **Step 6: Commit**

```bash
git add ipo_watcher/run.py tests/test_run.py
git commit -m "feat: wire discovery, download, and alert into one orchestrated run"
```

---

### Task 8: README, `.env`, and cron setup (manual smoke test against the live site)

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: `ipo_watcher.run.main` (Task 7) as the thing being documented/run.
- Produces: nothing new in code — this task is the human-operated verification step for the whole plan.

- [ ] **Step 1: Write `README.md`**

```markdown
# ipo-watcher

Watches SEBI's Public Issues filings (DRHP/RHP/Prospectus/Addenda/Corrigenda)
and emails an alert when a new one appears.

## Setup

    uv sync
    cp .env.example .env
    # edit .env with your SMTP credentials (a Gmail app password works)

## Run once

    uv run python -m ipo_watcher.run

Check `logs/ipo_watcher.log` for what happened, `data/ipo_watcher.db` for
stored filings, and `data/pdfs/` for downloaded documents.

## Schedule with cron

    crontab -e

Add (runs Mon/Wed/Fri at 9:00 AM IST — set `CRON_TZ` if the machine running
cron isn't already on IST; not all cron implementations honor `CRON_TZ`, so
adjust the hour to your box's local-time equivalent of 9 AM IST if not):

    CRON_TZ=Asia/Kolkata
    0 9 * * 1,3,5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.run >> logs/cron.log 2>&1

(Cron day-of-week: 1=Mon, 3=Wed, 5=Fri.)

This isn't installed anywhere yet — install it on whichever server actually
hosts this long-term (not a throwaway/dev session), once deployed.
```

- [ ] **Step 2: Manual smoke test — real run #1**

```bash
cp .env.example .env   # fill in real SMTP creds before this step
uv run python -m ipo_watcher.run
```

Expected: `logs/ipo_watcher.log` shows filings parsed and no unhandled exception; `data/ipo_watcher.db` has rows in `filings`/`companies`; `data/pdfs/` has downloaded PDF files; one email arrives listing the filings found.

- [ ] **Step 3: Manual smoke test — real run #2 (idempotency check)**

```bash
uv run python -m ipo_watcher.run
```

Expected: no new rows in `filings` (same count as after run #1), no second email sent — confirms `dedup_key` correctly prevents re-alerting on unchanged SEBI listings.

- [ ] **Step 4: Install the cron line on the actual deployment target (deferred)**

Not done in a dev/sandbox session — install the crontab line from `README.md` (adjusted to the real absolute paths, including the `.venv` path `uv sync` creates) once this is deployed to wherever it will actually run long-term (e.g. the free server the user plans to host on). Wait for the next scheduled time (Mon/Wed/Fri 9 AM IST), then check `logs/cron.log` shows a run occurred with no errors.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: setup, manual smoke test, and cron instructions"
```

---

## Verification (whole plan)

- `uv run pytest tests/ -v` passes all 22 tests with zero live network calls.
- Manual run #1 against the real SEBI site produces DB rows, downloaded PDFs, and exactly one email — left to the user to run themselves (needs their real SMTP credentials).
- Manual run #2 immediately after produces zero new rows and zero emails (idempotency) — also left to the user.
- A cron-triggered run (Task 8, Step 4) is deferred until deployment to the actual hosting target and is not part of this session's verification.
