# GMP Poller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `ipo_watcher/gmp_source.py` — fetch live GMP data from IPO Guru's free API, match each entry to a known company, and store a snapshot — so future work can read a fresh `GMP %` before computing a predicted Listing Gain.

**Architecture:** One new module mirroring `sebi_source.py`'s existing fetch/parse split (pure parsing function, separate HTTP-fetching function, same retry constants/pattern), plus a `match_company` lookup against the existing `companies` table and an orchestrator (`poll_gmp_snapshots`) that ties fetch → parse → match → store together. A new, separate cron entry point (`python -m ipo_watcher.gmp_source`) — not folded into `run.py`'s existing cycle.

**Tech Stack:** Python stdlib, `requests` (already a dependency via `sebi_source.py`), `pytest` + `unittest.mock` (already the project's test style, per `tests/test_sebi_source.py`).

**Spec:** `docs/superpowers/specs/2026-10-11-gmp-poller-design.md`

## Global Constraints

- No new HTTP library — reuse `requests`, matching `sebi_source.py`.
- `fetch_gmp_listing()`'s `X-API-KEY` header value comes from `config.IPO_GURU_API_KEY` (new env var, loaded via `.env`/`python-dotenv` like every other credential in this project — see `ipo_watcher/config.py`).
- `response_json` is always the wrapper object `{"success", "plan", "count", "data": [...]}` — never a bare array. `parse_gmp_listing` reads `response_json["data"]`.
- Match the real IPO entry fields exactly: `entry["name"]` for company name (never `display_name`), `entry["gmp"]["percentage"]` and `entry["gmp"]["price"]` for the two GMP values, both arriving as strings that must be parsed to `float`.
- An entry with no `gmp` key, or a `gmp.percentage` that fails to parse as `float`, is dropped silently (not raised) — never aborts the rest of the batch.
- `match_company` **never creates a company row** — only `db.upsert_filing` → `db._get_or_create_company` does that. A name with no match is logged and skipped.
- No fuzzy/edit-distance name matching in v1 — exact match on the normalized name only.
- Transient network failures (`requests.ConnectionError`, `requests.Timeout`) retry using the same `MAX_RETRIES`/`RETRY_SLEEP_SECONDS` constants/pattern as `sebi_source.py`. Any HTTP status error (`429`, `401`, `5xx`) is **never retried** — it aborts the poll cycle cleanly (logged, zero snapshots written, process does not crash).
- The poller is a **separate** cron entry, not part of `run.py`'s Mon/Wed/Fri discover/download/alert cycle.

## Review Focus

- A company name that differs only by case or extra whitespace (e.g. `"ACME ROBOTICS LIMITED "` vs. the stored `"Acme Robotics Ltd"`) — a reasonable person expects `match_company` to still find it, not silently skip a real match over formatting noise.
- An IPO Guru entry for a company that has no row in `companies` at all (not yet discovered via a SEBI filing) — must be skipped cleanly, never raise `sqlite3.IntegrityError` by calling `record_gmp_snapshot` with no valid `company_id`.
- A `429` (rate-limited) response arriving partway through what would otherwise be a normal poll — the whole cycle must abort with **zero** snapshots written (no partial-batch inconsistency) and the process must exit normally, not crash with an unhandled traceback.
- An entry whose `gmp.percentage` is present but is an empty string or non-numeric garbage (not just a missing `gmp` key) — must be dropped individually, same as a missing `gmp` key, never raise out of `parse_gmp_listing` and abort parsing of the remaining entries.
- Two different companies whose GMP both appear in the same poll response — each must be matched and stored against its own `company_id`, never cross-attributed (the same cross-company-leak risk already pinned for `get_latest_gmp_pct` in the snapshot-schema plan, now on the write path instead of the read path).

---

### Task 1: `GmpRow` and `parse_gmp_listing` (pure parsing, no network, no DB)

**Files:**
- Create: `ipo_watcher/gmp_source.py`
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `GmpRow` — `@dataclass` with fields `company_name: str`, `gmp_pct: float`, `gmp_rupees: float | None`.
  - `parse_gmp_listing(response_json: dict) -> list[GmpRow]`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_gmp_source.py`:

```python
from ipo_watcher.gmp_source import GmpRow, parse_gmp_listing


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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ipo_watcher.gmp_source'`.

- [x] **Step 3: Write the minimal implementation**

Create `ipo_watcher/gmp_source.py`:

```python
import logging
import time
from dataclasses import dataclass

import requests

from . import config

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3
IPOS_URL = "https://www.ipoguru.in/api/v1/ipos"

log = logging.getLogger("ipo_watcher.gmp_source")


@dataclass
class GmpRow:
    company_name: str
    gmp_pct: float
    gmp_rupees: float | None = None


def parse_gmp_listing(response_json):
    rows = []
    for entry in response_json.get("data", []):
        gmp = entry.get("gmp")
        if not gmp:
            continue
        try:
            gmp_pct = float(gmp["percentage"])
        except (KeyError, TypeError, ValueError):
            continue
        gmp_rupees = None
        if gmp.get("price") is not None:
            try:
                gmp_rupees = float(gmp["price"])
            except (TypeError, ValueError):
                gmp_rupees = None
        rows.append(GmpRow(
            company_name=entry["name"],
            gmp_pct=gmp_pct,
            gmp_rupees=gmp_rupees,
        ))
    return rows
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -v`
Expected: PASS (5 tests)

- [x] **Step 5: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "feat: add GmpRow and parse_gmp_listing to gmp_source"
```

---

### Task 2: `match_company`

**Files:**
- Modify: `ipo_watcher/gmp_source.py`
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db._get_or_create_company(conn, name) -> (company_id, is_new)` (all in `ipo_watcher/db.py`, already used this way by `tests/test_db.py`'s GMP tests).
- Produces: `match_company(conn, raw_name: str) -> int | None`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_gmp_source.py`:

```python
import pytest
from ipo_watcher import db
from ipo_watcher.gmp_source import match_company


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k match_company -v`
Expected: FAIL — `ImportError: cannot import name 'match_company'`.

- [x] **Step 3: Implement `match_company`**

Append to `ipo_watcher/gmp_source.py`:

```python
import re

_SUFFIX_RE = re.compile(r"\b(ltd|limited|pvt|private|ipo)\b\.?", re.IGNORECASE)


def _normalize_name(name):
    name = name.strip().lower()
    name = _SUFFIX_RE.sub("", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return name.strip()


def match_company(conn, raw_name):
    target = _normalize_name(raw_name)
    for row in conn.execute("SELECT id, name FROM companies"):
        if _normalize_name(row["name"]) == target:
            return row["id"]
    return None
```

Move the `import re` to the top of the file alongside the other imports (keep imports grouped at the top, matching `sebi_source.py`'s style) rather than leaving it inline where shown above.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -k match_company -v`
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "feat: add match_company name resolution to gmp_source"
```

---

### Task 3: `fetch_gmp_listing` and `IPO_GURU_API_KEY` config

**Files:**
- Modify: `ipo_watcher/gmp_source.py`
- Modify: `ipo_watcher/config.py`
- Modify: `.env.example`
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `config.IPO_GURU_API_KEY` (new).
- Produces: `fetch_gmp_listing() -> dict` (the decoded JSON envelope, raises `requests.HTTPError` on any non-2xx status, raises `requests.RequestException` after exhausting retries on a connection/timeout failure).

- [x] **Step 1: Write the failing tests**

Append to `tests/test_gmp_source.py`:

```python
from unittest.mock import patch, MagicMock
import requests
from ipo_watcher.gmp_source import fetch_gmp_listing


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


def test_fetch_gmp_listing_does_not_retry_on_http_error(monkeypatch):
    monkeypatch.setattr("ipo_watcher.gmp_source.config.IPO_GURU_API_KEY", "test-key-123")
    fake_response = MagicMock()
    fake_response.raise_for_status.side_effect = requests.HTTPError("429 Client Error")
    with patch("ipo_watcher.gmp_source.requests.get", return_value=fake_response) as mock_get:
        with pytest.raises(requests.HTTPError):
            fetch_gmp_listing()
    assert mock_get.call_count == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k fetch_gmp_listing -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_gmp_listing'`.

- [x] **Step 3: Add the config variable**

In `ipo_watcher/config.py`, after `ALERT_TO`:

```python
IPO_GURU_API_KEY = os.environ.get("IPO_GURU_API_KEY", "")
```

In `.env.example`, after `ALERT_TO=you@gmail.com`:

```
# IPO Guru API key (free tier: https://www.ipoguru.in/ipo-gmp-details-developer-api)
# IPO_GURU_API_KEY=your-key-here
```

- [x] **Step 4: Implement `fetch_gmp_listing`**

Append to `ipo_watcher/gmp_source.py` (note: `requests` and `time` are already imported from Task 1's implementation):

```python
def fetch_gmp_listing():
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                IPOS_URL,
                headers={"User-Agent": USER_AGENT, "X-API-KEY": config.IPO_GURU_API_KEY},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
            continue
        resp.raise_for_status()
        return resp.json()
    raise last_exc
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -k fetch_gmp_listing -v`
Expected: PASS (3 tests)

- [x] **Step 6: Commit**

```bash
git add ipo_watcher/gmp_source.py ipo_watcher/config.py .env.example tests/test_gmp_source.py
git commit -m "feat: add fetch_gmp_listing and IPO_GURU_API_KEY config"
```

---

### Task 4: `poll_gmp_snapshots`, cron entry point, and README

**Files:**
- Modify: `ipo_watcher/gmp_source.py`
- Modify: `README.md`
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `fetch_gmp_listing()`, `parse_gmp_listing()`, `match_company()` (all this file, Tasks 1-3), `db.record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None)` (from `ipo_watcher/db.py`), `run.setup_logging()` (from `ipo_watcher/run.py`), `config.DB_PATH`.
- Produces: `poll_gmp_snapshots(conn) -> dict` with keys `"matched"`, `"unmatched"`, `"skipped_errors"` (all `int`); a `main()` entry point for `python -m ipo_watcher.gmp_source`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_gmp_source.py`:

```python
from ipo_watcher.gmp_source import poll_gmp_snapshots


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


def test_poll_gmp_snapshots_aborts_cleanly_on_http_error(conn):
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    with patch(
        "ipo_watcher.gmp_source.fetch_gmp_listing",
        side_effect=requests.HTTPError("429 Client Error"),
    ):
        result = poll_gmp_snapshots(conn)
    assert result == {"matched": 0, "unmatched": 0, "skipped_errors": 0}
    assert conn.execute("SELECT COUNT(*) AS n FROM gmp_snapshots").fetchone()["n"] == 0
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k poll_gmp_snapshots -v`
Expected: FAIL — `ImportError: cannot import name 'poll_gmp_snapshots'`.

- [x] **Step 3: Implement `poll_gmp_snapshots` and the cron entry point**

Append to `ipo_watcher/gmp_source.py`:

```python
from . import db


def poll_gmp_snapshots(conn):
    try:
        response_json = fetch_gmp_listing()
    except requests.HTTPError as exc:
        log.error("GMP poll aborted: HTTP error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0}
    except requests.RequestException as exc:
        log.error("GMP poll aborted: network error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0}

    rows = parse_gmp_listing(response_json)
    matched = unmatched = 0
    for row in rows:
        company_id = match_company(conn, row.company_name)
        if company_id is None:
            unmatched += 1
            log.warning("No company match for GMP entry %r", row.company_name)
            continue
        db.record_gmp_snapshot(conn, company_id, row.gmp_pct, row.gmp_rupees)
        matched += 1
    result = {"matched": matched, "unmatched": unmatched, "skipped_errors": 0}
    log.info("GMP poll: %d matched, %d unmatched", matched, unmatched)
    return result


def main():
    from . import run as _run
    import os

    _run.setup_logging()
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    try:
        poll_gmp_snapshots(conn)
    except Exception:
        log.exception("Unhandled error in gmp_source poll")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

Move the `from . import db` and the `main()`-only imports (`run as _run`, `os`) to the top of the file alongside the other imports, matching the rest of the file's style, rather than leaving them inline where shown above.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -k poll_gmp_snapshots -v`
Expected: PASS (4 tests)

- [x] **Step 5: Add the cron line to README.md**

In `README.md`, after the existing "Schedule with cron" section's final paragraph ("This isn't installed anywhere yet..."), add:

```markdown
### GMP poller (separate, more frequent cron)

The GMP poller (`ipo_watcher.gmp_source`) is a **separate** cron entry from
the filing collector above — different cadence, different failure domain.
IPO Guru's free API tier allows only 10 requests/day, so this runs roughly
every 45 minutes during market hours, using the entire daily budget:

    CRON_TZ=Asia/Kolkata
    */45 9-15 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1

Requires `IPO_GURU_API_KEY` set in `.env` (see `.env.example`). Like the
filing collector's cron line, this isn't installed anywhere yet — install
it by hand on whichever server actually hosts this long-term.
```

- [x] **Step 6: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 30 plus the 16 new ones added across this plan's 4 tasks).

- [x] **Step 7: Commit**

```bash
git add ipo_watcher/gmp_source.py README.md tests/test_gmp_source.py
git commit -m "feat: add poll_gmp_snapshots orchestrator, cron entry point, and README cadence"
```
