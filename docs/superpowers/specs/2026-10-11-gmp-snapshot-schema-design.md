# GMP Snapshot Storage — Design

> Design doc for the storage shape only. Does not include the live poller
> that writes to it, the live-collector wiring, or a cron schedule — see
> "Explicitly out of scope" below.

## Context

`docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md`
froze the Listing Gain formula (`predict_listing_gain()` in
`research/ipo-scoring-validation/score.py`) and deferred its DB schema as
a separate design pass, since the formula turned out to need only one
live field (`GMP %`), not the four-field `market_signals` shape the
original Phase 2 ideation doc had sketched. This doc is that pass.

The original ideation doc also flagged, independently of scoring:
"Track subscription/GMP as a time series even before scoring exists ...
turns every IPO the collector already tracks into free future training
data." This design keeps that value — full history, not just the latest
value — even though nothing reads the history back yet.

## The schema

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

Added to `ipo_watcher/db.py`'s existing `SCHEMA` string, alongside
`companies`/`filings` — same `CREATE TABLE IF NOT EXISTS` style, no
migrations framework, matching the rest of this file.

- **Linked to `company_id`, not `filings.id`.** GMP is a signal about the
  company's pending IPO as a whole, not about any one DRHP/RHP/addendum
  document — a company can have several filings but only one evolving
  GMP.
- **`gmp_pct` is `NOT NULL`** — it is the only field the formula actually
  consumes. `gmp_rupees` is nullable and stored purely for
  traceability/debugging, the same instinct that made `filings` store
  both `pdf_path` and `pdf_sha256` rather than just one.
- **No `UNIQUE`/dedup key, deliberately.** Every poll is its own row, not
  idempotent-by-content like `filings`. Polling the same IPO twice in one
  hour and getting the same `GMP %` back is still two real observations
  in the time series, not a duplicate to collapse — this is the
  "keep every snapshot" choice from the brainstorming session, not an
  oversight.
- **`polled_at` via `_now_iso()`** — IST, matching every other timestamp
  in this project (`discovered_at`, `notified_at`, `first_seen_at`).
  Never a naive `datetime.now()`/UTC.

## Read/write functions (`ipo_watcher/db.py`)

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

`get_latest_gmp_pct` is the function a future wiring pass calls before
`predict_listing_gain()`. **No "full history for a company" query is
added yet** — nothing reads the history back until a future feature (a
GMP-trend view, a refit job) actually needs it; add that query then, per
YAGNI, not speculatively now.

## Explicitly out of scope (deferred to later, separate design passes)

- **The live poller** that actually calls IPO Guru's API and writes a
  snapshot (a `gmp_source.py`, matching `sebi_source.py`'s existing
  pattern) — still blocked on getting a real IPO Guru API key (see the
  scoring-formula design doc's "Live data source" section).
- **Matching a live-tracked IPO name back to a `companies.id` row.** IPO
  Guru's name for a company will not exactly match SEBI's filing name —
  the same fuzzy-name-matching problem `research/ipo-scoring-validation/
  build_dataset.py`'s `_normalize_name()` already solves for the
  ipo-radar/Kaggle cross-check, but not yet solved for live data. Flagged
  now so it is not forgotten when the poller actually gets built.
- **A cron/polling-cadence decision** — how often to call IPO Guru
  (their docs suggest every 15-30 minutes during market hours; the
  300/day free-tier limit needs checking against whatever cadence is
  chosen) and whether that's a new cron line or folded into the existing
  collector's run.
- **Wiring `get_latest_gmp_pct` + `predict_listing_gain()` into `run.py`
  or the email** — this doc is storage only.

## What this spec asks to be implemented next

1. Add the `gmp_snapshots` table to `ipo_watcher/db.py`'s `SCHEMA`
   string.
2. Add `record_gmp_snapshot` and `get_latest_gmp_pct` to `db.py`,
   following the existing function style (plain `conn.execute`, explicit
   `conn.commit()`, `_now_iso()` for timestamps).
3. Tests: a round-trip test (write a snapshot, read it back via
   `get_latest_gmp_pct`), a "most recent wins" test (two snapshots for
   the same company, same company's latest `polled_at` comes back), and
   a "no snapshot yet returns `None`" test — matching this project's
   existing `tests/` style for `db.py`.

## Open questions for the next pass

1. No company-resolution step exists yet for a live-tracked IPO name, so
   `record_gmp_snapshot` assumes a valid `company_id` is already known
   when the (not-yet-built) poller calls it. The fuzzy-name-matching
   problem above is real and unsolved.
2. Should a `gmp_snapshots` row ever get deleted (e.g. once an IPO is
   long listed and past its 24-month outcome window)? Not decided —
   default is to keep every row forever unless/until this becomes an
   actual storage problem, which at this project's volume (a few hundred
   IPOs a year, a handful of polls each) is not imminent.
