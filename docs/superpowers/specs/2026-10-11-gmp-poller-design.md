# GMP Poller — Design

> Design doc for `gmp_source.py`: fetching live GMP data, matching it to a
> known company, and storing it. Does not include reading the data back out
> to compute or email a predicted Listing Gain — see "Explicitly out of
> scope" below.

## Context

`docs/superpowers/specs/2026-10-11-gmp-snapshot-schema-design.md` built the
storage (`gmp_snapshots` table, `record_gmp_snapshot`/`get_latest_gmp_pct`)
but deliberately left the live poller, the name-matching problem, and the
cadence decision for a separate pass. This is that pass.

**Corrects a wrong number from an earlier spec.** The scoring-formula
design doc (`2026-09-20-listing-gain-scoring-formula-design.md`) stated IPO
Guru's free tier as "300 requests/day, 15/min." That was never verified
against the real pricing page. The actual free tier, confirmed directly from
`https://www.ipoguru.in/ipo-gmp-details-developer-api`:

| Tier | Price | Req/day | Req/min | Notes |
|---|---|---|---|---|
| **Free** | ₹0 | **10** | **1** | "Evaluation only" — IPO calendar, details, **live GMP** |
| Basic | ₹99/mo | 250 | 10 | + full company info |
| Standard | ₹299/mo | 1,000 | 20 | + GMP history, subscription, anchor data |
| Pro | ₹499/mo | 3,000 | 40 | + intraday GMP, OFS, unlisted prices |

This design targets the **free tier** (10 req/day) — a deliberate choice
to validate the live-wiring approach before spending money on it. That
earlier doc will be corrected to match this table.

## Architecture

One new module, `ipo_watcher/gmp_source.py`, mirroring `sebi_source.py`'s
existing fetch/parse split (same file, same conventions — `requests`,
module-level `TIMEOUT`/`MAX_RETRIES`/`RETRY_SLEEP_SECONDS` constants, a
`@dataclass` row type):

- **`fetch_gmp_listing()`** — one `GET https://www.ipoguru.in/api/v1/ipos`
  (no `status` filter — a single unfiltered call returns every IPO IPO Guru
  currently tracks, open/upcoming/closed alike, which is the cheapest way
  to use the 10/day budget: one call captures everything instead of one
  call per status). Auth via an `X-API-KEY` header, read from
  `config.IPO_GURU_API_KEY`.
- **`parse_gmp_listing(response_json)`** — pure function. `response_json`
  is the full decoded response body, which is **always a wrapper object**,
  never a bare array — confirmed from IPO Guru's reference page:
  ```json
  {"success": true, "plan": "standard", "count": 1, "data": [ /* IPO objects */ ]}
  ```
  `parse_gmp_listing` reads `response_json["data"]` as the list of IPO
  entries to parse; `success`/`plan`/`count` are ignored (a `success: false`
  body only ever arrives alongside a non-2xx status, which `fetch_gmp_listing`
  already turns into an `HTTPError` before `parse_gmp_listing` ever sees the
  body — see "Error handling" below). Returns a list of
  `GmpRow(company_name, gmp_pct, gmp_rupees)`, reading each entry in `data`'s
  **confirmed real fields** (from
  `https://www.ipoguru.in/ipo-gmp-details-developer-api#reference`):
  - `company_name` ← `entry["name"]` (the legal company name — **not**
    `display_name`, which is a separate shortened UI label that won't
    match SEBI's filing names).
  - `gmp_pct` ← `entry["gmp"]["percentage"]`, parsed from its string form
    (`"5.07"`) to `float`.
  - `gmp_rupees` ← `entry["gmp"]["price"]`, same string-to-float parsing
    (`"7.5"`).
  - An entry with **no `gmp` key at all** (an IPO before GMP exists for
    it — common for a just-announced "Upcoming" issue) is dropped, not an
    error — `gmp_pct` is `NOT NULL` in storage, so there is nothing valid
    to record yet. Same treatment for a `gmp.percentage` that fails to
    parse as a float.
  - Every other field on the entry (`slug`, `type`, `status`, `price_band`,
    `issue_size`, `listing_gain_percent`, etc.) is ignored — this poller
    only ever needed `GMP %`, per the scoring formula's own conclusion.
- **`match_company(conn, raw_name)`** — normalizes `raw_name` (lowercase,
  strip whitespace/punctuation, drop common suffixes: `Ltd`, `Limited`,
  `Pvt`, `IPO`) and compares it against every `companies.name` normalized
  the same way. Returns a `company_id` on an exact match, `None`
  otherwise. **Never creates a company row** — only SEBI filings do that,
  via the existing `upsert_filing` → `_get_or_create_company` path. The
  normalization logic is reimplemented here, not imported from
  `research/ipo-scoring-validation/build_dataset.py`'s `_normalize_name()`
  — `research/` stays deliberately decoupled from the live collector, per
  this project's existing convention.
- **`poll_gmp_snapshots(conn)`** — the orchestrator. Fetches, parses,
  calls `match_company` per row, and `db.record_gmp_snapshot` on a hit.
  Returns `{"matched": int, "unmatched": int, "skipped_errors": int}` for
  the log line — same shape as `run.py`'s existing top-level functions.

## Name matching

Exact match on the normalized name only — **no fuzzy/edit-distance
matching in v1.** A miss means either the IPO hasn't been discovered via a
SEBI filing yet, or the two sources' names genuinely don't normalize to the
same string. Either way: log it, skip it, move on. This is YAGNI by
design — fuzzy matching adds real complexity (threshold tuning, false-match
risk) that isn't justified until real unmatched-rate data shows exact
matching is actually losing usable data.

## Error handling

- **Transient network failure** (connection error, timeout): retried using
  `sebi_source.py`'s existing `MAX_RETRIES`/`RETRY_SLEEP_SECONDS` constants
  and pattern.
- **`429` (rate limit exceeded)**: **not retried.** Logged, and the whole
  poll cycle aborts cleanly. Retrying a 429 would spend another call from
  the same exhausted budget on the same failure — the free tier gives no
  room for that.
- **Any other HTTP error** (`401` expired/bad key, `5xx`): logged with the
  status code, cycle aborts the same way as a 429.
- **One bad row** (missing `gmp_pct`, unparseable): dropped individually
  inside `parse_gmp_listing`, counted in the orchestrator's
  `skipped_errors`. Never aborts the rest of the batch.

## Cadence & invocation

A **new, separate cron line**, not folded into `run.py`'s existing
Mon/Wed/Fri discover/download/alert cycle — different cadence (frequent vs.
3x/week), different failure domain (a GMP-poll failure must never block
filing discovery or the email alert, and vice versa).

`uv run python -m ipo_watcher.gmp_source`, roughly every 45 minutes during
market hours (9:00–15:45 IST, weekdays) — about 10 calls/day, which is the
**entire** free-tier daily budget with no headroom left for ad-hoc or retry
calls that day. Documented in `README.md` by hand, same "install on
whatever server ends up hosting this" convention as the existing cron line
— installation is a manual step, not automated.

**Config addition**: `IPO_GURU_API_KEY` in `ipo_watcher/config.py` (env
var, loaded via `.env`/`python-dotenv` like every other credential in this
project), and a commented-out example line in `.env.example`.

## Testing

No real API key exists yet, so everything is tested against **mocked HTTP
responses** — matches this project's existing test style
(`tests/test_sebi_source.py` already mocks `sebi_source.py`'s HTTP calls
the same way).

- `parse_gmp_listing()`: a handful of hand-written JSON fixtures, matching
  the real nested shape — a normal row with a populated `gmp` block, a row
  with **no `gmp` key at all** (the common "too early" case), a row whose
  `gmp.percentage` is an unparseable string, and a row with extra unknown
  fields (IPO Guru may add fields later; parsing must ignore what it
  doesn't need beyond `name`/`gmp.percentage`/`gmp.price`).
- `match_company()`: against the existing `companies` fixture pattern from
  `tests/test_db.py` — exact match, no match, and a name differing only by
  a suffix (`"Acme Robotics Ltd"` vs. `"Acme Robotics"`).
- `poll_gmp_snapshots()`: end-to-end with `fetch_gmp_listing` monkeypatched
  to a fake return value, writing into an in-memory DB — covering
  all-matched, partial-matched, and a `429` response aborting the cycle
  cleanly with no snapshots written.

## Explicitly out of scope (deferred to a separate design pass)

- **Reading the stored data back out** — calling
  `get_latest_gmp_pct`/`predict_listing_gain()` from `run.py`, deciding
  when in an IPO's lifecycle to compute/refresh a score, and adding it to
  the email. This doc stops at "the data is fetched, matched, and stored."
- **Upgrading off the free tier** — if 10/day with no retry headroom turns
  out to be too tight in practice (e.g. the 1-req/min cap collides with
  the 45-minute cadence during a transient retry), revisit the Basic tier
  (₹99/mo, 250/day) then — not speculatively now.
- **Fuzzy name matching** — only if real unmatched-rate data justifies it
  (see "Name matching" above).

## What this spec asks to be implemented next

1. `ipo_watcher/gmp_source.py` — `GmpRow` dataclass, `fetch_gmp_listing()`,
   `parse_gmp_listing()`, `match_company()`, `poll_gmp_snapshots()`.
2. `IPO_GURU_API_KEY` added to `ipo_watcher/config.py` and `.env.example`.
3. `tests/test_gmp_source.py` — the three test groups described above.
4. `README.md` — the new cron line, documented by hand like the existing
   one.
5. Correct the "300 requests/day, 15/min" claim in
   `docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md`'s
   "Live data source" section to match the real free-tier numbers in this
   doc's Context section.

## Open questions for the next pass

1. ~~The exact field names in a real, authenticated IPO Guru response are
   unverified~~ — **resolved, 2026-10-11**: confirmed from IPO Guru's own
   API reference page (`#reference`), not just their marketing copy. Field
   names are now pinned in the Architecture section above. Still unverified:
   whether a *live, authenticated* response actually matches this
   documented shape exactly — the reference page is still documentation,
   not a real response body. A small adjustment may still be needed once a
   real key exists, but the shape is no longer a guess.
2. Whether 45-minute cadence is actually the right granularity once this
   runs for real — GMP moves fastest in the last day or two before
   listing, so a future refinement might poll more often for IPOs close to
   their listing date and less often otherwise, within the same daily
   budget. Not designed now; today's flat cadence is the simplest thing
   that could work, per this design's own cadence discussion.
