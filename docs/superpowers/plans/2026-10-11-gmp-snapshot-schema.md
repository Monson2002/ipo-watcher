# GMP Snapshot Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add storage for GMP (grey market premium) snapshots over time, so a future live-wiring pass has somewhere to write poll results and somewhere for the scoring formula to read the latest value from.

**Architecture:** One additive table (`gmp_snapshots`) appended to `ipo_watcher/db.py`'s existing `SCHEMA` string, plus two small functions (`record_gmp_snapshot`, `get_latest_gmp_pct`) following the file's existing plain-`sqlite3`, no-ORM style. No new files; everything lands in `ipo_watcher/db.py` and `tests/test_db.py`, which already hold the equivalent `companies`/`filings` logic this mirrors.

**Tech Stack:** Python stdlib `sqlite3`, `pytest` (already the project's test runner — `uv run pytest tests/ -v`).

**Spec:** `docs/superpowers/specs/2026-10-11-gmp-snapshot-schema-design.md`

## Global Constraints

- No ORM, no migrations framework — plain `CREATE TABLE IF NOT EXISTS` SQL in the `SCHEMA` string, exactly like `companies`/`filings`.
- Every timestamp goes through `_now_iso()` (IST via `config.IST`) — never naive `datetime.now()` or UTC.
- `gmp_snapshots` has **no** `UNIQUE`/dedup key — every poll is its own row, deliberately not idempotent-by-content like `filings`.
- `gmp_pct` is `NOT NULL` (the only field the formula consumes); `gmp_rupees` is nullable (traceability only).
- Linked to `company_id`, never `filings.id` — GMP is a signal about the company's pending IPO as a whole.
- No "full history for a company" query yet — only `get_latest_gmp_pct` is needed now; add a history query only when something actually reads it back (YAGNI, per the spec's explicit call).

## Review Focus

- A `record_gmp_snapshot` call with a `company_id` that does not exist in `companies` — `connect()` already turns on `PRAGMA foreign_keys = ON`, so a reasonable person expects a clear `sqlite3.IntegrityError`, not a silently orphaned row.
- Two snapshots for the same company recorded with the **identical** `polled_at` value (possible if two polls land in the same second) — `get_latest_gmp_pct`'s `ORDER BY polled_at DESC, id DESC` must still deterministically return the one inserted last, not an arbitrary row.
- Two different companies each with snapshots — `get_latest_gmp_pct` must return the asking company's own latest value, never another company's (a scoring context makes this the worst kind of bug: a confidently wrong number for the wrong IPO).
- A real `gmp_pct` of `0.0` or a negative value (an IPO trading below its grey-market-implied price, or exactly at par) — must round-trip and compare as a real value, never be treated as "no data" the way `None` is.
- `gmp_rupees` omitted entirely (the common case — not every source will supply it) — `record_gmp_snapshot` must default it to `NULL` and the row must still read back correctly.

---

### Task 1: GMP snapshot storage in `ipo_watcher/db.py`

**Files:**
- Modify: `ipo_watcher/db.py` (append to `SCHEMA`, add two functions)
- Test: `tests/test_db.py` (append tests; uses the existing `conn` fixture already defined there)

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db._get_or_create_company(conn, name)` → `(company_id, is_new)` — all already defined in `ipo_watcher/db.py`.
- Produces:
  - `db.record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None) -> None`
  - `db.get_latest_gmp_pct(conn, company_id) -> float | None`

- [x] **Step 1: Write the failing test for the schema**

In `tests/test_db.py`, extend the existing table-presence assertion (do not add a new test function — this is the same assertion the file already makes, just checking one more table):

```python
def test_init_db_creates_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"companies", "filings", "gmp_snapshots"} <= tables
```

- [x] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: FAIL — `gmp_snapshots` is not in `tables` yet.

- [x] **Step 3: Add the table to `SCHEMA`**

In `ipo_watcher/db.py`, append to the `SCHEMA` string (after the `idx_filings_company` index, still inside the same triple-quoted string):

```sql
CREATE TABLE IF NOT EXISTS gmp_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    gmp_rupees  REAL,
    gmp_pct     REAL NOT NULL,
    polled_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gmp_snapshots_company ON gmp_snapshots(company_id);
```

- [x] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: add gmp_snapshots table to schema"
```

- [x] **Step 6: Write the failing round-trip test**

Append to `tests/test_db.py`:

```python
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
```

- [x] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "gmp" -v`
Expected: FAIL with `AttributeError: module 'ipo_watcher.db' has no attribute 'record_gmp_snapshot'` (and similarly for `get_latest_gmp_pct`).

- [x] **Step 8: Implement `record_gmp_snapshot` and `get_latest_gmp_pct`**

In `ipo_watcher/db.py`, add after `set_pdf_info`:

```python
def record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None):
    conn.execute(
        "INSERT INTO gmp_snapshots (company_id, gmp_rupees, gmp_pct, polled_at) VALUES (?, ?, ?, ?)",
        (company_id, gmp_rupees, gmp_pct, _now_iso()),
    )
    conn.commit()


def get_latest_gmp_pct(conn, company_id):
    row = conn.execute(
        "SELECT gmp_pct FROM gmp_snapshots WHERE company_id = ? ORDER BY polled_at DESC, id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row["gmp_pct"] if row else None
```

- [x] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k "gmp" -v`
Expected: PASS (4 tests)

- [x] **Step 10: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: add record_gmp_snapshot and get_latest_gmp_pct"
```

- [x] **Step 11: Write the failing test for same-timestamp tie-breaking**

Append to `tests/test_db.py`. This pins the `ORDER BY polled_at DESC, id DESC` tiebreaker — without the `id DESC` clause, two rows with an identical `polled_at` return in an undefined order:

```python
def test_get_latest_gmp_pct_breaks_ties_by_insertion_order(conn, monkeypatch):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    monkeypatch.setattr(db, "_now_iso", lambda: "2026-10-11T10:00:00+05:30")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=10.0)
    db.record_gmp_snapshot(conn, company_id, gmp_pct=20.0)
    assert db.get_latest_gmp_pct(conn, company_id) == 20.0
```

- [x] **Step 12: Run the test to verify it fails or passes for the wrong reason**

Run: `uv run pytest tests/test_db.py::test_get_latest_gmp_pct_breaks_ties_by_insertion_order -v`
Expected: PASS already, since Step 8's query already includes `id DESC`. This step exists to confirm that tiebreaker is actually exercised, not to drive new code — if it fails, the query in Step 8 is missing the `id DESC` clause and must be fixed before continuing.

- [x] **Step 13: Write the failing test for cross-company scoping**

```python
def test_get_latest_gmp_pct_does_not_leak_between_companies(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    db.record_gmp_snapshot(conn, company_a, gmp_pct=10.0)
    db.record_gmp_snapshot(conn, company_b, gmp_pct=99.0)
    assert db.get_latest_gmp_pct(conn, company_a) == 10.0
    assert db.get_latest_gmp_pct(conn, company_b) == 99.0
```

- [x] **Step 14: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_get_latest_gmp_pct_does_not_leak_between_companies -v`
Expected: PASS (the `WHERE company_id = ?` clause in Step 8 already scopes correctly; this step exists to pin that behavior, same reasoning as Step 12).

- [x] **Step 15: Write the failing test for the foreign-key constraint**

```python
def test_record_gmp_snapshot_rejects_unknown_company_id(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.record_gmp_snapshot(conn, company_id=999999, gmp_pct=10.0)
```

- [x] **Step 16: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_record_gmp_snapshot_rejects_unknown_company_id -v`
Expected: PASS — `connect()` already runs `PRAGMA foreign_keys = ON`, and `company_id INTEGER NOT NULL REFERENCES companies(id)` in Step 3's DDL already enforces this. This step exists to pin that the constraint is real and active for this table specifically, not to drive new code.

- [x] **Step 17: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 23 plus the 8 new ones added in this task).

- [x] **Step 18: Commit**

```bash
git add tests/test_db.py
git commit -m "test: pin gmp_snapshots tie-breaking, scoping, and FK behavior"
```
