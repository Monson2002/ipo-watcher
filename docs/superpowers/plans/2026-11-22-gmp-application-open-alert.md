# GMP Application-Open Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Post-implementation note, 2026-11-22:** this plan's dedup design
> (`has_open_date_snapshot`, described in Tasks 1 and 2 below) was found
> buggy by review after all four tasks were executed — it would almost
> never have fired in practice. It was replaced with a corrected design
> (`application_open_notifications`, success-gated). The tasks below are
> kept as a historical record of what was executed, not a guide to the
> current code — read the spec (linked below, itself amended) and the
> execution ledger (`.superpowers/sdd/2026-11-22-gmp-application-open-alert/progress.md`)
> for what actually shipped.

**Goal:** Send a one-time email the day an IPO's bidding/application window actually opens, carrying the GMP% at that moment, instead of the user having to infer "can I apply today" from filing-discovery or GMP-availability timestamps that can land days earlier.

**Architecture:** `gmp_source.poll_gmp_snapshots` already fetches and stores a GMP snapshot per tracked company on every poll; it gains one more field to read from the API response (`open_date`) and one more self-limiting detection check (does this poll see `open_date == today` for the first time for this company). `run.py` gets a thin alert-sending wrapper so `gmp_source.py` stays pure fetch/store, matching how `send_gmp_update_alerts` is already wired. `notifier.py` gets one more builder trio, matching its two existing ones exactly.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), `requests`, `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-22-gmp-application-open-alert-design.md`

## Global Constraints

- All timestamps/dates are computed via `config.IST` (`zoneinfo.ZoneInfo("Asia/Kolkata")`) — never naive `datetime.now()`/UTC (see `CLAUDE.md`'s architecture notes on `run.py`).
- `gmp_snapshots` has no migration path — new columns are added directly to the `SCHEMA` string, safe only because nothing is deployed to a real server with existing data that must survive a migration (per the table's original design doc's "note on schema changes", reaffirmed in this plan's spec).
- `db.py` functions use `conn.row_factory = sqlite3.Row` semantics already set in `connect()` — access row fields by name (`row["field"]`), not by index.
- Every new public function follows the existing True/False-on-outcome contract already used by `send_alert`, `send_gmp_update_alert`, and `send_gmp_update_alerts` — never raises on a send failure, just returns `False` and logs.
- `gmp_source.py` stays pure fetch/store; alert-sending logic is only ever invoked via `run.py` from `gmp_source.main()`, never `notifier` called directly from `gmp_source.py` (spec's "Delivery" section, matching the existing `send_gmp_update_alerts` wiring).

## Review Focus

- **A company's `open_date` is `None` or malformed** (IPO Guru hasn't set it yet, or sends an unexpected shape) — must never crash the poll; treated as "not opening today," same as the existing `close_date`/`is_listed` defensive parsing. Covered in Task 2.
- **The poller runs multiple times on the same calendar day** (confirmed real: 10 scheduled runs per weekday) — must send the application-open email exactly once per company, not once per poll. Covered in Task 2's dedup tests.
- **A company's `open_date` was "today" on a previous day's poll (clock/timezone drift) and is being compared against a fresh `today`** — the comparison must use `config.IST`-based "today," not host-machine local time, since GitHub Actions runners are UTC. Covered in Task 2 by reusing the existing `datetime.datetime.now(config.IST).date().isoformat()` pattern already proven in `send_gmp_update_alerts`.
- **A company's window opens on the same poll its GMP also crosses the 5pp movement threshold** — both alerts must fire independently, with no interference between `send_application_open_alerts` and the existing `send_gmp_update_alerts`. Covered in Task 4.
- **`notifier.send_application_open_alert([])` (no companies opened this poll)** — must return `True` and send nothing, matching `send_alert`/`send_gmp_update_alert`'s existing no-op contract. Covered in Task 3.

---

### Task 1: `open_date` storage in `gmp_snapshots`

**Files:**
- Modify: `ipo_watcher/db.py:31-51` (schema), `ipo_watcher/db.py:143-157` (`record_gmp_snapshot`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `db.record_gmp_snapshot(conn, company_id, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None, open_date=None)` — one new optional kwarg. `db.has_open_date_snapshot(conn, company_id, open_date) -> bool` — new function, used by Task 2.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py` (near the existing `test_record_gmp_snapshot_stores_close_date_and_is_listed` test):

```python
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


def test_has_open_date_snapshot_returns_false_with_no_prior_snapshot(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    assert db.has_open_date_snapshot(conn, company_id, "2026-11-18") is False


def test_has_open_date_snapshot_returns_true_once_recorded(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4, open_date="2026-11-18")
    assert db.has_open_date_snapshot(conn, company_id, "2026-11-18") is True


def test_has_open_date_snapshot_does_not_match_a_different_date(conn):
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.4, open_date="2026-11-18")
    assert db.has_open_date_snapshot(conn, company_id, "2026-11-19") is False


def test_has_open_date_snapshot_does_not_leak_between_companies(conn):
    company_a, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    company_b, _ = db._get_or_create_company(conn, "Gaurik Fashions Limited")
    db.record_gmp_snapshot(conn, company_a, gmp_pct=18.4, open_date="2026-11-18")
    assert db.has_open_date_snapshot(conn, company_b, "2026-11-18") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k open_date -v`
Expected: `FAIL` — `record_gmp_snapshot() got an unexpected keyword argument 'open_date'` (first two tests), then `AttributeError: module 'ipo_watcher.db' has no attribute 'has_open_date_snapshot'` (remaining three).

- [ ] **Step 3: Add the `open_date` column to the schema**

In `ipo_watcher/db.py`, replace the `gmp_snapshots` table definition (lines 31-39):

```python
CREATE TABLE IF NOT EXISTS gmp_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    gmp_rupees  REAL,
    gmp_pct     REAL NOT NULL,
    close_date  TEXT,
    is_listed   INTEGER,
    open_date   TEXT,
    polled_at   TEXT NOT NULL
);
```

- [ ] **Step 4: Update `record_gmp_snapshot` and add `has_open_date_snapshot`**

Replace `ipo_watcher/db.py:143-157`:

```python
def record_gmp_snapshot(
    conn, company_id, gmp_pct, gmp_rupees=None, close_date=None, is_listed=None,
    open_date=None,
):
    conn.execute(
        """INSERT INTO gmp_snapshots
           (company_id, gmp_rupees, gmp_pct, close_date, is_listed, open_date, polled_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            gmp_rupees,
            gmp_pct,
            close_date,
            None if is_listed is None else int(is_listed),
            open_date,
            _now_iso(),
        ),
    )
    conn.commit()


def has_open_date_snapshot(conn, company_id, open_date):
    row = conn.execute(
        "SELECT 1 FROM gmp_snapshots WHERE company_id = ? AND open_date = ? LIMIT 1",
        (company_id, open_date),
    ).fetchone()
    return row is not None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: all tests `PASS`, including the five new ones (no regressions in the existing `gmp_snapshots`/`record_gmp_snapshot` tests, since every new parameter is optional and defaults preserve current behavior).

- [ ] **Step 6: Commit**

```bash
git add ipo_watcher/db.py tests/test_db.py
git commit -m "feat: store open_date on gmp_snapshots and add has_open_date_snapshot"
```

---

### Task 2: Detect the open-date transition in `gmp_source.py`

**Files:**
- Modify: `ipo_watcher/gmp_source.py:42-48` (`GmpRow`), `ipo_watcher/gmp_source.py:66-96` (`parse_gmp_listing`), `ipo_watcher/gmp_source.py:140-173` (`poll_gmp_snapshots`)
- Test: `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `db.record_gmp_snapshot(..., open_date=None)` and `db.has_open_date_snapshot(conn, company_id, open_date) -> bool` from Task 1.
- Produces: `GmpRow.open_date: str | None`. `poll_gmp_snapshots(conn)`'s result dict gains an `"opens_today"` key — a list of `{"company_name": str, "gmp_pct": float}` dicts, empty when nothing opened this poll. This is what Task 4 passes to `run.send_application_open_alerts`.

- [ ] **Step 1: Write the failing parsing tests**

Add to `tests/test_gmp_source.py` (near the existing `test_parse_gmp_listing_reads_close_date_and_is_listed` test):

```python
def test_parse_gmp_listing_reads_open_date():
    response = _envelope([
        {
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "5.07", "price": "7.5"},
            "open_date": "2026-11-18",
        }
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].open_date == "2026-11-18"


def test_parse_gmp_listing_treats_malformed_open_date_as_none():
    response = _envelope([
        {"name": "Bad Date Ltd", "gmp": {"percentage": "5.0"}, "open_date": "18-Nov-2026"},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].open_date is None


def test_parse_gmp_listing_defaults_open_date_to_none_when_absent():
    response = _envelope([
        {"name": "Upcoming Co Ltd", "gmp": {"percentage": "5.0"}},
    ])
    rows = parse_gmp_listing(response)
    assert rows[0].open_date is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k "reads_open_date or malformed_open_date or defaults_open_date" -v`
Expected: `FAIL` — `AttributeError: 'GmpRow' object has no attribute 'open_date'`.

- [ ] **Step 3: Add `open_date` to `GmpRow` and parse it**

In `ipo_watcher/gmp_source.py`, replace the `GmpRow` dataclass (lines 42-48):

```python
@dataclass
class GmpRow:
    company_name: str
    gmp_pct: float
    gmp_rupees: float | None = None
    close_date: str | None = None
    is_listed: bool | None = None
    open_date: str | None = None
```

In `parse_gmp_listing`, replace the `rows.append(...)` block (lines 89-95):

```python
        rows.append(GmpRow(
            company_name=company_name,
            gmp_pct=gmp_pct,
            gmp_rupees=gmp_rupees,
            close_date=_parse_close_date(entry.get("close_date")),
            is_listed=_parse_is_listed(entry.get("is_listed")),
            open_date=_parse_close_date(entry.get("open_date")),
        ))
```

`_parse_close_date` is reused as-is: it validates a `YYYY-MM-DD`-shaped string and returns `None` for anything else, which is exactly the validation `open_date` needs too (same format, same "defensive, no crash on weird upstream data" requirement from the spec's "New data captured" section).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -k "reads_open_date or malformed_open_date or defaults_open_date" -v`
Expected: all three `PASS`.

- [ ] **Step 5: Write the failing detection tests**

Add to `tests/test_gmp_source.py` (near the existing `test_poll_gmp_snapshots_passes_close_date_and_is_listed_through` test). These tests monkeypatch `gmp_source._today` directly (a new module-level helper added in Step 7) rather than patching stdlib `datetime` globally — patching `datetime.datetime.now` itself would leak into every other piece of code in the process that reads the clock during the test:

```python
def test_poll_gmp_snapshots_flags_company_whose_window_opens_today(conn, monkeypatch):
    monkeypatch.setattr(gmp_source, "_today", lambda: "2026-11-18")
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [{
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "18.4"},
            "open_date": "2026-11-18",
        }],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result["opens_today"] == [{"company_name": "Acme Robotics Ltd", "gmp_pct": 18.4}]
    assert db.has_open_date_snapshot(conn, company_id, "2026-11-18") is True


def test_poll_gmp_snapshots_does_not_reflag_same_day_open(conn, monkeypatch):
    monkeypatch.setattr(gmp_source, "_today", lambda: "2026-11-18")
    company_id, _ = db._get_or_create_company(conn, "Acme Robotics Ltd")
    db.record_gmp_snapshot(conn, company_id, gmp_pct=18.0, open_date="2026-11-18")
    fake_json = {
        "success": True,
        "data": [{
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "18.4"},
            "open_date": "2026-11-18",
        }],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result["opens_today"] == []


def test_poll_gmp_snapshots_does_not_flag_company_opening_a_different_day(conn, monkeypatch):
    monkeypatch.setattr(gmp_source, "_today", lambda: "2026-11-18")
    db._get_or_create_company(conn, "Acme Robotics Ltd")
    fake_json = {
        "success": True,
        "data": [{
            "name": "Acme Robotics Ltd",
            "gmp": {"percentage": "18.4"},
            "open_date": "2026-11-20",
        }],
    }
    with patch("ipo_watcher.gmp_source.fetch_gmp_listing", return_value=fake_json):
        result = poll_gmp_snapshots(conn)
    assert result["opens_today"] == []
```

Also update every existing exact-dict-equality assertion on `poll_gmp_snapshots`'s result in `tests/test_gmp_source.py` (there are seven: `test_poll_gmp_snapshots_all_matched`, `test_poll_gmp_snapshots_partial_match`, `test_poll_gmp_snapshots_counts_skipped_errors_for_malformed_rows`, `test_poll_gmp_snapshots_skips_cleanly_when_api_key_missing`, `test_poll_gmp_snapshots_aborts_cleanly_on_http_error`, `test_poll_gmp_snapshots_does_not_crash_on_non_dict_response`, `test_poll_gmp_snapshots_aborts_cleanly_on_network_error`) to add `"opens_today": []` to the expected dict, e.g.:

```python
    assert result == {
        "matched": 1, "unmatched": 0, "skipped_errors": 0, "failed": False, "opens_today": [],
    }
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k "opens_today or flags_company_whose_window or does_not_reflag_same_day or does_not_flag_company_opening" -v`
Expected: `FAIL` — `KeyError: 'opens_today'` on the new tests, and dict-mismatch `AssertionError`s on the seven updated ones.

- [ ] **Step 7: Implement the detection**

Add `import datetime` to the top of `ipo_watcher/gmp_source.py` (alongside the existing `import logging` etc.), and add a small module-level helper right after the existing `_CLOSE_DATE_RE` constant (so tests can monkeypatch it directly instead of patching stdlib `datetime`):

```python
def _today():
    return datetime.datetime.now(config.IST).date().isoformat()
```

Replace `poll_gmp_snapshots` (lines 140-173):

```python
def poll_gmp_snapshots(conn):
    if not config.IPO_GURU_API_KEY:
        log.error("GMP poll skipped: IPO_GURU_API_KEY is not configured")
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0, "failed": True, "opens_today": []}
    try:
        response_json = fetch_gmp_listing()
    except requests.HTTPError as exc:
        log.error("GMP poll aborted: HTTP error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0, "failed": True, "opens_today": []}
    except requests.RequestException as exc:
        log.error("GMP poll aborted: network error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0, "failed": True, "opens_today": []}

    today = _today()
    rows = parse_gmp_listing(response_json)
    total_entries = len(_extract_entries(response_json))
    skipped_errors = total_entries - len(rows)
    matched = unmatched = 0
    opens_today = []
    for row in rows:
        company_id = match_company(conn, row.company_name)
        if company_id is None:
            unmatched += 1
            log.warning("No company match for GMP entry %r", row.company_name)
            continue
        # open_date is a fixed value for an IPO's lifetime, so "has any
        # prior snapshot already recorded this exact open_date" is enough
        # to dedupe across the ~10 same-day polls — checked before writing
        # this new snapshot so the alert fires exactly once per company.
        if row.open_date == today and not db.has_open_date_snapshot(conn, company_id, row.open_date):
            opens_today.append({"company_name": row.company_name, "gmp_pct": row.gmp_pct})
        db.record_gmp_snapshot(
            conn, company_id, row.gmp_pct, row.gmp_rupees,
            close_date=row.close_date, is_listed=row.is_listed, open_date=row.open_date,
        )
        matched += 1
    result = {
        "matched": matched, "unmatched": unmatched, "skipped_errors": skipped_errors,
        "failed": False, "opens_today": opens_today,
    }
    log.info(
        "GMP poll: %d matched, %d unmatched, %d skipped (malformed)",
        matched, unmatched, skipped_errors,
    )
    return result
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py -v`
Expected: all tests `PASS` (49 existing + 6 new = 55).

- [ ] **Step 9: Commit**

```bash
git add ipo_watcher/gmp_source.py tests/test_gmp_source.py
git commit -m "feat: detect when an IPO's application window opens today"
```

---

### Task 3: `notifier.py` application-open email builders

**Files:**
- Modify: `ipo_watcher/notifier.py` (append after `send_gmp_update_alert`, end of file)
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: nothing new from other tasks (uses the existing `_HTML_WRAPPER`, `_send_email`, `html`, `config` already imported in this file).
- Produces: `notifier.build_application_open_subject(rows)`, `notifier.build_application_open_body(rows)`, `notifier.build_application_open_html(rows)`, `notifier.send_application_open_alert(rows) -> bool`. Each row is `{"company_name": str, "gmp_pct": float}` — exactly what Task 2's `poll_gmp_snapshots` puts in `"opens_today"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notifier.py` (near the existing `GMP_UPDATE_ROWS` fixture and its tests):

```python
APPLICATION_OPEN_ROWS = [
    {"company_name": "Acme Robotics Ltd", "gmp_pct": 18.4},
    {"company_name": "Gaurik Fashions Limited", "gmp_pct": 9.0},
]


def test_build_application_open_subject():
    assert notifier.build_application_open_subject(APPLICATION_OPEN_ROWS) == "IPO Watcher: 2 IPOs open for bidding today"
    assert notifier.build_application_open_subject(APPLICATION_OPEN_ROWS[:1]) == "IPO Watcher: 1 IPO opens for bidding today"


def test_build_application_open_body_shows_company_and_gmp():
    body = notifier.build_application_open_body(APPLICATION_OPEN_ROWS)
    assert "Acme Robotics Ltd opens for bidding today - GMP at 18.4%" in body
    assert "Gaurik Fashions Limited opens for bidding today - GMP at 9.0%" in body


def test_build_application_open_html_shows_company_and_gmp():
    html_body = notifier.build_application_open_html(APPLICATION_OPEN_ROWS)
    assert "Acme Robotics Ltd" in html_body
    assert "18.4%" in html_body
    assert "Gaurik Fashions Limited" in html_body
    assert "9.0%" in html_body


def test_send_application_open_alert_returns_true_and_sends_nothing_when_no_rows():
    with patch("ipo_watcher.notifier.requests.post") as mock_post:
        ok = notifier.send_application_open_alert([])
    assert ok is True
    mock_post.assert_not_called()


def test_send_application_open_alert_posts_to_resend(monkeypatch):
    monkeypatch.setattr(notifier.config, "RESEND_API_KEY", "re_test_key", raising=False)
    monkeypatch.setattr(notifier.config, "ALERT_FROM", "onboarding@resend.dev")
    monkeypatch.setattr(notifier.config, "ALERT_TO", "me@example.com")

    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()

    with patch("ipo_watcher.notifier.requests.post", return_value=fake_response) as mock_post:
        ok = notifier.send_application_open_alert(APPLICATION_OPEN_ROWS)

    assert ok is True
    mock_post.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_notifier.py -k application_open -v`
Expected: `FAIL` — `AttributeError: module 'ipo_watcher.notifier' has no attribute 'build_application_open_subject'` (and similarly for each other new function as its test runs).

- [ ] **Step 3: Implement the builders**

Append to the end of `ipo_watcher/notifier.py`:

```python
def build_application_open_subject(rows):
    count = len(rows)
    noun = "IPO" if count == 1 else "IPOs"
    verb = "opens" if count == 1 else "open"
    return f"IPO Watcher: {count} {noun} {verb} for bidding today"


def _application_open_fragment(r):
    return f"{r['company_name']} opens for bidding today - GMP at {r['gmp_pct']:.1f}%"


def build_application_open_body(rows):
    lines = [_application_open_fragment(r) for r in rows]
    return "\n".join(lines).strip() + "\n"


def build_application_open_html(rows):
    cards = []
    for r in rows:
        cards.append(f"""
      <div style="border-bottom:1px solid #e5e7eb;padding:16px 0;">
        <div style="font-size:16px;font-weight:600;color:#111827;margin:0 0 4px;">{html.escape(r['company_name'])}</div>
        <div style="font-size:14px;color:#374151;">Opens for bidding today - GMP at {r['gmp_pct']:.1f}%</div>
      </div>""")
    count = len(rows)
    subtitle = f"{count} IPO{'' if count == 1 else 's'} open{'s' if count == 1 else ''} for bidding today"
    return _HTML_WRAPPER.format(subtitle=subtitle, cards="".join(cards))


def send_application_open_alert(rows):
    """Sends one batched email for all application-window-opens-today
    rows. Same True/False-on-failure contract as send_alert/
    send_gmp_update_alert."""
    if not rows:
        return True
    return _send_email(
        build_application_open_subject(rows),
        build_application_open_body(rows),
        build_application_open_html(rows),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_notifier.py -v`
Expected: all tests `PASS` (28 existing + 6 new = 34).

- [ ] **Step 5: Commit**

```bash
git add ipo_watcher/notifier.py tests/test_notifier.py
git commit -m "feat: add the application-open-day email builders"
```

---

### Task 4: Wire the alert into `run.py` and `gmp_source.main()`

**Files:**
- Modify: `ipo_watcher/run.py` (new function, placed immediately before `send_gmp_update_alerts` at line 149), `ipo_watcher/gmp_source.py:176-194` (`main`)
- Test: `tests/test_run.py`, `tests/test_gmp_source.py`

**Interfaces:**
- Consumes: `notifier.send_application_open_alert(rows) -> bool` from Task 3; `poll_gmp_snapshots(conn)`'s `"opens_today"` key from Task 2.
- Produces: `run.send_application_open_alerts(rows) -> bool` — the only new public symbol; `gmp_source.main()`'s behavior changes (calls it), but its own signature is unchanged.

- [ ] **Step 1: Write the failing `run.py` test**

Add to `tests/test_run.py` (near the existing `send_pending_alert`/`send_gmp_update_alerts` tests):

```python
def test_send_application_open_alerts_forwards_to_notifier_and_returns_true():
    rows = [{"company_name": "Acme Robotics Ltd", "gmp_pct": 18.4}]
    with patch("ipo_watcher.run.notifier.send_application_open_alert", return_value=True) as mock_send:
        ok = run.send_application_open_alerts(rows)
    mock_send.assert_called_once_with(rows)
    assert ok is True


def test_send_application_open_alerts_forwards_failure():
    with patch("ipo_watcher.run.notifier.send_application_open_alert", return_value=False):
        ok = run.send_application_open_alerts([{"company_name": "X", "gmp_pct": 1.0}])
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run.py -k application_open -v`
Expected: `FAIL` — `AttributeError: module 'ipo_watcher.run' has no attribute 'send_application_open_alerts'`.

- [ ] **Step 3: Add the wrapper**

In `ipo_watcher/run.py`, insert immediately before `def send_gmp_update_alerts(conn):` (line 149):

```python
def send_application_open_alerts(rows):
    """Thin wrapper so gmp_source.py's cron entry calls into run.py for
    alerting (matching send_gmp_update_alerts), keeping gmp_source.py as
    pure fetch/store."""
    return notifier.send_application_open_alert(rows)


```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run.py -v`
Expected: all tests `PASS`.

- [ ] **Step 5: Write the failing `gmp_source.main()` wiring test**

Add to `tests/test_gmp_source.py` (near the existing `test_main_returns_true_when_poll_and_update_alerts_both_succeed` test):

```python
def test_main_sends_application_open_alert_when_poll_reports_opens_today(tmp_path, monkeypatch):
    monkeypatch.setattr(gmp_source.config, "DB_PATH", str(tmp_path / "main_opens.db"))
    poll_result = {
        "matched": 1, "unmatched": 0, "skipped_errors": 0, "failed": False,
        "opens_today": [{"company_name": "Acme Robotics Ltd", "gmp_pct": 18.4}],
    }
    with patch("ipo_watcher.gmp_source.poll_gmp_snapshots", return_value=poll_result), \
         patch("ipo_watcher.run.send_application_open_alerts", return_value=True) as mock_open_send, \
         patch("ipo_watcher.run.send_gmp_update_alerts", return_value=True):
        ok = gmp_source.main()
    mock_open_send.assert_called_once_with(poll_result["opens_today"])
    assert ok is True


def test_main_returns_false_when_application_open_alert_send_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(gmp_source.config, "DB_PATH", str(tmp_path / "main_opens_fail.db"))
    poll_result = {
        "matched": 1, "unmatched": 0, "skipped_errors": 0, "failed": False,
        "opens_today": [{"company_name": "Acme Robotics Ltd", "gmp_pct": 18.4}],
    }
    with patch("ipo_watcher.gmp_source.poll_gmp_snapshots", return_value=poll_result), \
         patch("ipo_watcher.run.send_application_open_alerts", return_value=False), \
         patch("ipo_watcher.run.send_gmp_update_alerts", return_value=True):
        ok = gmp_source.main()
    assert ok is False


def test_main_does_not_call_application_open_alerts_when_nothing_opened_today(tmp_path, monkeypatch):
    monkeypatch.setattr(gmp_source.config, "DB_PATH", str(tmp_path / "main_no_opens.db"))
    poll_result = {
        "matched": 1, "unmatched": 0, "skipped_errors": 0, "failed": False, "opens_today": [],
    }
    with patch("ipo_watcher.gmp_source.poll_gmp_snapshots", return_value=poll_result), \
         patch("ipo_watcher.run.send_application_open_alerts") as mock_open_send, \
         patch("ipo_watcher.run.send_gmp_update_alerts", return_value=True):
        ok = gmp_source.main()
    mock_open_send.assert_not_called()
    assert ok is True
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmp_source.py -k main_sends_application_open_or_application_open_alert_send_fails_or_main_does_not_call -v`
(If that `-k` expression doesn't match due to pytest's keyword matching syntax, run the three test names individually instead: `uv run pytest tests/test_gmp_source.py::test_main_sends_application_open_alert_when_poll_reports_opens_today tests/test_gmp_source.py::test_main_returns_false_when_application_open_alert_send_fails tests/test_gmp_source.py::test_main_does_not_call_application_open_alerts_when_nothing_opened_today -v`)
Expected: `FAIL` on the two that assert `mock_open_send.assert_called_once_with(...)` / return `False` — `main()` never calls `send_application_open_alerts` yet, so these mocks are never invoked and the assertions fail; the "does not call" test passes trivially already (nothing to break), which is fine — it becomes a real regression guard once Step 7 is implemented.

- [ ] **Step 7: Wire it into `gmp_source.main()`**

In `ipo_watcher/gmp_source.py`, replace the body of `main()` (lines 183-188):

```python
    ok = True
    try:
        poll_result = poll_gmp_snapshots(conn)
        if poll_result.get("failed"):
            ok = False
        if poll_result.get("opens_today") and not _run.send_application_open_alerts(poll_result["opens_today"]):
            ok = False
        if not _run.send_gmp_update_alerts(conn):
            ok = False
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmp_source.py tests/test_run.py -v`
Expected: all tests `PASS`.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: all tests `PASS` (180 before this plan + roughly 17 new across the four tasks = ~197).

- [ ] **Step 10: Commit**

```bash
git add ipo_watcher/run.py ipo_watcher/gmp_source.py tests/test_run.py tests/test_gmp_source.py
git commit -m "feat: send an email the day an IPO's application window opens"
```
