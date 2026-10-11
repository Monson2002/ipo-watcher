# GMP Update Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send a follow-up email whenever a tracked IPO's GMP moves ≥5 percentage points from the value last actually emailed, with extra emphasis when the company's bidding window closes today, and permanently stop once the company has listed or its bidding window has passed.

**Architecture:** New DB state (`gmp_notifications` table; `close_date`/`is_listed` columns on `gmp_snapshots`) plus a new orchestration function in `run.py` that reads it, reusing the existing GMP poller's cron cadence (zero new cron entries, zero new API calls) and a small pure refactor of `notifier.py` to share SMTP plumbing between the existing filing alert and this new update alert.

**Tech Stack:** Python stdlib `sqlite3`, `pytest` + `unittest.mock` (already this project's conventions).

**Spec:** `docs/superpowers/specs/2026-10-11-gmp-update-alerts-design.md`

## Global Constraints

- `gmp_notifications` has **no** `UNIQUE`/dedup key — every notification is its own row, same philosophy as `gmp_snapshots`.
- Movement threshold is **≥5.0 percentage points absolute** (`abs(latest - last_notified) >= 5.0`), never a relative/percent-of-percent comparison.
- **No daily cap** — every poll that crosses the threshold sends its own email and resets the baseline. Do not add debouncing.
- Stopping condition is checked **before** the threshold check, and is permanent once true: `is_listed is True` OR (`close_date` is set AND today's IST date is strictly after `close_date`). A `close_date` of exactly today does **not** stop it — that's the day that gets the "closing today" emphasis, not the day it stops.
- `gmp_snapshots` keeps recording **every** company on **every** poll regardless of the stopping condition — the stop applies only to whether `send_gmp_update_alerts` emails about it, never to `poll_gmp_snapshots` storing it.
- `record_gmp_notification` (baseline reset) is called **only** for companies whose movement actually crossed the threshold — never for a company that was skipped (stopped, or below threshold).
- Zero new cron entries. `gmp_source.py`'s `main()` calls `run.send_gmp_update_alerts(conn)` immediately after `poll_gmp_snapshots(conn)`, same connection, same `try`/`finally`.
- This is a schema change to an already-shipped table (`gmp_snapshots`), done by directly editing the `SCHEMA` string (not an `ALTER TABLE`) — safe only because nothing is deployed yet (see the spec's "A note on schema changes"). Don't add migration machinery; just edit the string.

## Review Focus

- A company whose `close_date` is **exactly today** must still get an update email (with the closing-today note) — only a `close_date` strictly *before* today stops it. An off-by-one here (stopping one day too early) would silently suppress the single most important email this feature exists to send.
- A company with `is_listed = True` must be filtered out **before** the threshold check even runs, no matter how large its GMP movement looks — a person who already got their company's IPO listed should never get a "meaningful move" email about it again.
- GMP **decreasing** by ≥5pp must trigger exactly like an increase (the comparison is `abs(...)`, not `latest - last_notified >= 5.0`) — a one-directional comparison would silently miss every warning sign of a deflating grey market.
- Two or more companies crossing the threshold in the same poll must produce **one** batched email, not one `send_gmp_update_alerts`-internal call per company — matches `send_pending_alert`'s existing one-email-per-run convention; a naive per-company loop calling the notifier each time would spam multiple SMTP sends per cron run.
- A company **below** threshold must leave its `gmp_notifications` baseline untouched — resetting it anyway would let a GMP value drift several small increments past what the user was actually told about, without ever crossing 5pp against the (silently-moved) baseline.

---

### Task 1: `gmp_notifications` table, `gmp_snapshots` schema additions, and their DB functions

**Files:**
- Modify: `ipo_watcher/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `_now_iso()`, `connect`, `init_db` (all pre-existing in this file).
- Produces:
  - `record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None) -> None` (extends the existing function's signature — two new optional params, default `None` so every existing call site keeps working unchanged).
  - `get_latest_close_date(conn, company_id) -> str | None`
  - `get_latest_is_listed(conn, company_id) -> bool | None`
  - `record_gmp_notification(conn, company_id, gmp_pct) -> None`
  - `get_last_notified_gmp_pct(conn, company_id) -> float | None`
  - `get_notified_company_ids(conn) -> list[int]`

- [ ] **Step 1: Write the failing test for the schema**

In `tests/test_db.py`, extend the existing table-presence assertion:

```python
def test_init_db_creates_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"companies", "filings", "gmp_snapshots", "gmp_notifications"} <= tables
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: FAIL — `gmp_notifications` is not in `tables` yet.

- [ ] **Step 3: Update the `SCHEMA` string**

In `ipo_watcher/db.py`, replace the existing `gmp_snapshots` block (currently lines 31-39) with:

```sql
CREATE TABLE IF NOT EXISTS gmp_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    gmp_rupees  REAL,
    gmp_pct     REAL NOT NULL,
    close_date  TEXT,
    is_listed   INTEGER,
    polled_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gmp_snapshots_company ON gmp_snapshots(company_id);

CREATE TABLE IF NOT EXISTS gmp_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    gmp_pct     REAL NOT NULL,
    notified_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gmp_notifications_company ON gmp_notifications(company_id);
```

(This is the existing `gmp_snapshots` table with two new nullable columns —
`close_date`, `is_listed` — inserted before `polled_at`, plus the new
`gmp_notifications` table appended after it, still inside the same
triple-quoted `SCHEMA` string.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_db.py::test_init_db_creates_tables -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: add gmp_notifications table and close_date/is_listed columns"
```

- [ ] **Step 6: Write the failing tests for `close_date`/`is_listed` storage**

Append to `tests/test_db.py`:

```python
def test_record_gmp_snapshot_stores_close_date_and_is_listed(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4, close_date="2026-10-20", is_listed=False)
    assert db.get_latest_close_date(conn, company_id) == "2026-10-20"
    assert db.get_latest_is_listed(conn, company_id) is False


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
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "close_date or is_listed" -v`
Expected: FAIL with `TypeError: record_gmp_snapshot() got an unexpected keyword argument 'close_date'` (and similarly, `get_latest_close_date`/`get_latest_is_listed` not defined).

- [ ] **Step 8: Implement the storage and accessors**

In `ipo_watcher/db.py`, replace the existing `record_gmp_snapshot` function with:

```python
def record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None):
    conn.execute(
        """INSERT INTO gmp_snapshots
           (company_id, gmp_rupees, gmp_pct, close_date, is_listed, polled_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            gmp_rupees,
            gmp_pct,
            close_date,
            None if is_listed is None else int(is_listed),
            _now_iso(),
        ),
    )
    conn.commit()
```

Then add, after `get_latest_gmp_pct`:

```python
def get_latest_close_date(conn, company_id):
    row = conn.execute(
        "SELECT close_date FROM gmp_snapshots WHERE company_id = ? ORDER BY polled_at DESC, id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row["close_date"] if row else None


def get_latest_is_listed(conn, company_id):
    row = conn.execute(
        "SELECT is_listed FROM gmp_snapshots WHERE company_id = ? ORDER BY polled_at DESC, id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    if row is None or row["is_listed"] is None:
        return None
    return bool(row["is_listed"])
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k "close_date or is_listed" -v`
Expected: PASS (5 tests)

- [ ] **Step 10: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: store and read back close_date/is_listed on gmp_snapshots"
```

- [ ] **Step 11: Write the failing tests for `gmp_notifications`**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 12: Run the tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "notif" -v`
Expected: FAIL — `AttributeError: module 'ipo_watcher.db' has no attribute 'record_gmp_notification'` (and similarly for the other two functions).

- [ ] **Step 13: Implement the `gmp_notifications` functions**

In `ipo_watcher/db.py`, add after `get_latest_is_listed`:

```python
def record_gmp_notification(conn, company_id, gmp_pct):
    conn.execute(
        "INSERT INTO gmp_notifications (company_id, gmp_pct, notified_at) VALUES (?, ?, ?)",
        (company_id, gmp_pct, _now_iso()),
    )
    conn.commit()


def get_last_notified_gmp_pct(conn, company_id):
    row = conn.execute(
        "SELECT gmp_pct FROM gmp_notifications WHERE company_id = ? ORDER BY notified_at DESC, id DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    return row["gmp_pct"] if row else None


def get_notified_company_ids(conn):
    return [
        row["company_id"]
        for row in conn.execute("SELECT DISTINCT company_id FROM gmp_notifications")
    ]
```

- [ ] **Step 14: Run the tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k "notif" -v`
Expected: PASS (4 tests)

- [ ] **Step 15: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (the pre-existing 54 plus the 9 new ones from this task).

- [ ] **Step 16: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: add gmp_notifications table and its DB functions"
```

---

### Task 2: `gmp_source.py` captures `close_date`/`is_listed`

**Files:**
- Modify: `ipo_watcher/gmp_source.py`
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `db.record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None)` (Task 1).
- Produces: `GmpRow(company_name, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None)` — two new fields on the existing dataclass, both consumed by nothing yet outside this file (Task 4 reads them back from the DB, not from `GmpRow` directly).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gmp_source.py`:

```python
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


def test_parse_gmp_listing_defaults_close_date_and_is_listed_to_none_when_absent():
    response = _envelope([
        {"name": "Upcoming Co Ltd", "gmp": {"percentage": "5.0"}},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].close_date is None
    assert rows[0].is_listed is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k "close_date_and_is_listed" -v`
Expected: FAIL — `GmpRow(...)` raises `TypeError: __init__() got an unexpected keyword argument 'close_date'` (the dataclass doesn't have the field yet), and the second test fails because `rows[0].close_date` raises `AttributeError`.

- [ ] **Step 3: Add the fields and parsing**

In `ipo_watcher/gmp_source.py`, replace the `GmpRow` dataclass with:

```python
@dataclass
class GmpRow:
    company_name: str
    gmp_pct: float
    gmp_rupees: float | None = None
    close_date: str | None = None
    is_listed: bool | None = None
```

Then in `parse_gmp_listing`, change the `rows.append(...)` call to:

```python
        rows.append(GmpRow(
            company_name=company_name,
            gmp_pct=gmp_pct,
            gmp_rupees=gmp_rupees,
            close_date=entry.get("close_date"),
            is_listed=entry.get("is_listed"),
        ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -k "close_date_and_is_listed" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "feat: parse close_date and is_listed in gmp_source"
```

- [ ] **Step 6: Write the failing test for passing the fields through storage**

Append to `tests/test_gmp_source.py`:

```python
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
```

- [ ] **Step 7: Run the test to verify it fails**

Run: `uv run pytest tests/test_gmp_source.py::test_poll_gmp_snapshots_passes_close_date_and_is_listed_through -v`
Expected: FAIL — `assert None == "2026-10-20"` (the current `poll_gmp_snapshots` call to `record_gmp_snapshot` doesn't pass these two fields yet).

- [ ] **Step 8: Pass the fields through in `poll_gmp_snapshots`**

In `ipo_watcher/gmp_source.py`, change the `db.record_gmp_snapshot(...)` call inside `poll_gmp_snapshots` to:

```python
        db.record_gmp_snapshot(
            conn, company_id, row.gmp_pct, row.gmp_rupees,
            close_date=row.close_date, is_listed=row.is_listed,
        )
```

- [ ] **Step 9: Run the test to verify it passes**

Run: `uv run pytest tests/test_gmp_source.py::test_poll_gmp_snapshots_passes_close_date_and_is_listed_through -v`
Expected: PASS

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "feat: store close_date and is_listed from every poll"
```

---

### Task 3: `notifier.py` — shared SMTP primitive and the update-email builders

**Files:**
- Modify: `ipo_watcher/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: nothing from other tasks (pure email formatting, no DB).
- Produces:
  - `build_gmp_update_subject(rows) -> str`
  - `build_gmp_update_body(rows) -> str`
  - `send_gmp_update_alert(rows) -> bool`
  - Each row this family consumes is a dict with keys: `company_name`, `old_pct` (float), `new_pct` (float), `prediction_text` (str, already-formatted via `scoring.format_prediction`), `closing_today` (bool).

- [ ] **Step 1: Write the failing test that pins the refactor doesn't break `send_alert`**

This is the existing `tests/test_notifier.py::test_send_alert_returns_true_and_uses_smtp` — no new test needed here; it already asserts `smtplib.SMTP`/`starttls`/`login`/`send_message` are called correctly. Run it now to confirm it's green before touching anything:

Run: `uv run pytest tests/test_notifier.py::test_send_alert_returns_true_and_uses_smtp -v`
Expected: PASS (this is the baseline the refactor in Step 2 must not break).

- [ ] **Step 2: Refactor — extract `_send_email`, no behavior change**

In `ipo_watcher/notifier.py`, replace `send_alert` with:

```python
def _send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.ALERT_FROM
    msg["To"] = config.ALERT_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("SMTP send failed: %s", exc)
        return False


def send_alert(rows):
    """Sends one batched email for all rows. Returns True on success, False
    on any SMTP/network failure — caller decides whether to retry (by not
    marking these filings as notified)."""
    if not rows:
        return True
    return _send_email(build_email_subject(rows), build_email_body(rows))
```

- [ ] **Step 3: Run the existing notifier tests to confirm nothing broke**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: PASS (all tests, unchanged — this step has no new test of its own because it's a pure refactor; the existing suite is the proof).

- [ ] **Step 4: Commit**

```bash
git add ipo_watcher/notifier.py
git commit -m "refactor: extract _send_email from send_alert, no behavior change"
```

- [ ] **Step 5: Write the failing tests for the update-email builders**

Append to `tests/test_notifier.py`:

```python
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
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_notifier.py -k gmp_update -v`
Expected: FAIL — `AttributeError: module 'ipo_watcher.notifier' has no attribute 'build_gmp_update_subject'` (and similarly for the other two new functions).

- [ ] **Step 7: Implement the update-email builders**

In `ipo_watcher/notifier.py`, add after `build_email_body`:

```python
def build_gmp_update_subject(rows):
    count = len(rows)
    plural = "" if count == 1 else "s"
    return f"IPO Watcher: {count} GMP update{plural}"


def build_gmp_update_body(rows):
    lines = []
    for r in rows:
        lines.append(
            f"{r['company_name']}: GMP moved from {r['old_pct']:.1f}% to {r['new_pct']:.1f}%"
        )
        lines.append(r["prediction_text"])
        if r["closing_today"]:
            lines.append(
                "Bidding closes today - this is the last day to apply or revoke an application."
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def send_gmp_update_alert(rows):
    """Sends one batched email for all GMP movement rows. Same
    True/False-on-failure contract as send_alert."""
    if not rows:
        return True
    return _send_email(build_gmp_update_subject(rows), build_gmp_update_body(rows))
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: PASS (all tests, including the 5 new ones).

- [ ] **Step 9: Commit**

```bash
git add ipo_watcher/notifier.py tests/test_notifier.py
git commit -m "feat: add GMP update email builders and send_gmp_update_alert"
```

---

### Task 4: `run.py` orchestration — baseline recording, the threshold check, and wiring into the poller's cron cycle

**Files:**
- Modify: `ipo_watcher/run.py`
- Modify: `ipo_watcher/gmp_source.py` (one line, in `main()`)
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes:
  - `db.record_gmp_notification`, `db.get_last_notified_gmp_pct`, `db.get_notified_company_ids`, `db.get_latest_close_date`, `db.get_latest_is_listed` (Task 1).
  - `notifier.send_gmp_update_alert(rows)` (Task 3), where each row is `{"company_name", "old_pct", "new_pct", "prediction_text", "closing_today"}` exactly as Task 3 defined.
  - `scoring.predict_listing_gain`, `scoring.format_prediction` (pre-existing, already imported in `run.py`).
- Produces: `send_gmp_update_alerts(conn) -> None`.

- [ ] **Step 1: Write the failing test for baseline recording on the existing filing alert**

Append to `tests/test_run.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_run.py -k gmp_notification_baseline -v`
Expected: FAIL (first test) — `assert None == 18.0`, since nothing calls `record_gmp_notification` yet. The second test is expected to PASS already (nothing to break it), which is fine; it's here to pin the "no GMP yet" case once the next step is implemented, not to drive it.

- [ ] **Step 3: Record the baseline in `send_pending_alert`**

In `ipo_watcher/run.py`, replace `_gmp_prediction_text` and the loop body inside `send_pending_alert` with:

```python
def _gmp_prediction_text(conn, company_id):
    gmp_pct = db.get_latest_gmp_pct(conn, company_id)
    if gmp_pct is None:
        return None, "GMP not yet available."
    return gmp_pct, scoring.format_prediction(scoring.predict_listing_gain(gmp_pct))


def send_pending_alert(conn):
    rows = db.get_unnotified_filings(conn)
    if not rows:
        log.info("No new filings to alert on")
        return
    enriched_rows = []
    for r in rows:
        row = dict(r)
        gmp_pct, prediction_text = _gmp_prediction_text(conn, row["company_id"])
        row["gmp_prediction"] = prediction_text
        if gmp_pct is not None:
            db.record_gmp_notification(conn, row["company_id"], gmp_pct)
        enriched_rows.append(row)
    ok = notifier.send_alert(enriched_rows)
```

(The rest of `send_pending_alert` — the `if ok:`/`else:` block below — is unchanged.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_run.py -k gmp_notification_baseline -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass — this confirms the `_gmp_prediction_text` signature change didn't break
`test_discover_download_and_alert_end_to_end` or
`test_send_pending_alert_includes_predicted_gain_when_gmp_available`, both of which only inspect `sent_rows[...]["gmp_prediction"]`, not the function's return shape directly.

- [ ] **Step 6: Commit**

```bash
git add ipo_watcher/run.py tests/test_run.py
git commit -m "feat: record gmp_notification baseline when the filing alert includes a real GMP value"
```

- [ ] **Step 7: Write the failing tests for `send_gmp_update_alerts`' core threshold behavior**

Add `import datetime` to `tests/test_run.py`'s existing imports at the top
of the file (used by the two stopping-condition tests below). Then append
a small local helper to seed a company's full notification+snapshot
history, plus the threshold tests:

```python
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
```

- [ ] **Step 8: Run the tests to verify they fail**

Run: `uv run pytest tests/test_run.py -k "send_gmp_update_alerts" -v`
Expected: FAIL — `AttributeError: module 'ipo_watcher.run' has no attribute 'send_gmp_update_alerts'`.

- [ ] **Step 9: Implement the threshold check (stopping condition comes next, in Step 11)**

In `ipo_watcher/run.py`, add `GMP_MOVEMENT_THRESHOLD = 5.0` alongside the
existing `MAX_PAGES`/`REQUEST_DELAY_SECONDS` constants near the top of the
file, then add this function after `send_pending_alert`:

```python
def send_gmp_update_alerts(conn):
    rows = []
    for company_id in db.get_notified_company_ids(conn):
        last_notified = db.get_last_notified_gmp_pct(conn, company_id)
        latest_pct = db.get_latest_gmp_pct(conn, company_id)
        if latest_pct is None:
            continue
        if abs(latest_pct - last_notified) < GMP_MOVEMENT_THRESHOLD:
            continue
        company_row = conn.execute(
            "SELECT name FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        rows.append({
            "company_name": company_row["name"],
            "old_pct": last_notified,
            "new_pct": latest_pct,
            "prediction_text": scoring.format_prediction(scoring.predict_listing_gain(latest_pct)),
            "closing_today": False,
        })
        db.record_gmp_notification(conn, company_id, latest_pct)
    if rows:
        ok = notifier.send_gmp_update_alert(rows)
        if ok:
            log.info("Sent GMP update alert for %d compan(y/ies)", len(rows))
        else:
            log.error("Failed to send GMP update alert for %d compan(y/ies)", len(rows))
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `uv run pytest tests/test_run.py -k "send_gmp_update_alerts" -v`
Expected: PASS for the 4 tests from Step 7 (the stopping-condition tests from Step 12 below aren't written yet).

- [ ] **Step 11: Write the failing tests for the stopping condition**

Append to `tests/test_run.py`:

```python
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
```

- [ ] **Step 12: Run the tests to verify they fail**

Run: `uv run pytest tests/test_run.py -k "stops_once or closing_today_note" -v`
Expected: FAIL — the first two currently still send (no stopping condition implemented yet), the third currently sends but with `closing_today` hardcoded `False`.

- [ ] **Step 13: Implement the stopping condition and the closing-today flag**

In `ipo_watcher/run.py`, replace the body of the `for company_id in ...:` loop inside `send_gmp_update_alerts` with:

```python
    import datetime

    today = datetime.datetime.now(config.IST).date().isoformat()
    for company_id in db.get_notified_company_ids(conn):
        is_listed = db.get_latest_is_listed(conn, company_id)
        close_date = db.get_latest_close_date(conn, company_id)
        if is_listed or (close_date is not None and close_date < today):
            continue
        last_notified = db.get_last_notified_gmp_pct(conn, company_id)
        latest_pct = db.get_latest_gmp_pct(conn, company_id)
        if latest_pct is None:
            continue
        if abs(latest_pct - last_notified) < GMP_MOVEMENT_THRESHOLD:
            continue
        company_row = conn.execute(
            "SELECT name FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        rows.append({
            "company_name": company_row["name"],
            "old_pct": last_notified,
            "new_pct": latest_pct,
            "prediction_text": scoring.format_prediction(scoring.predict_listing_gain(latest_pct)),
            "closing_today": close_date == today,
        })
        db.record_gmp_notification(conn, company_id, latest_pct)
```

Move the `import datetime` to the top of the file alongside the existing
`import datetime` (it's already there, at line 1 — delete this inline
duplicate and just use the module-level import), rather than leaving it
inline where shown above.

- [ ] **Step 14: Run the tests to verify they pass**

Run: `uv run pytest tests/test_run.py -k "send_gmp_update_alerts or stops_once or closing_today_note" -v`
Expected: PASS (7 tests total from Steps 7 and 11)

- [ ] **Step 15: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass.

- [ ] **Step 16: Commit**

```bash
git add ipo_watcher/run.py tests/test_run.py
git commit -m "feat: add send_gmp_update_alerts with threshold check and stopping condition"
```

- [ ] **Step 17: Wire into the poller's cron cycle**

In `ipo_watcher/gmp_source.py`, in `main()`, change:

```python
    try:
        poll_gmp_snapshots(conn)
    except Exception:
```

to:

```python
    try:
        poll_gmp_snapshots(conn)
        _run.send_gmp_update_alerts(conn)
    except Exception:
```

(`_run` is already imported inside `main()` via `from . import run as _run`,
immediately above this block — no new import needed.)

- [ ] **Step 18: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass — `main()` itself has no direct test (matching
`run.py`'s own `main()`, which is also untested directly; both are thin
wiring over already-tested functions).

- [ ] **Step 19: Commit**

```bash
git add ipo_watcher/gmp_source.py
git commit -m "feat: call send_gmp_update_alerts from the GMP poller's cron cycle"
```
