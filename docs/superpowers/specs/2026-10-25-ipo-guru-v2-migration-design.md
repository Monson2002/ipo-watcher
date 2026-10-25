# IPO Guru API v2 Migration — Design

> Design doc for moving `gmp_source.py` off the retired `/api/v1` endpoint
> onto the real, verified `/api/v2` contract, now that a real API key
> exists. This is a correction pass, not a new subsystem — almost
> everything `gmp_source.py` already assumes turns out to be correct;
> one thing is now actively broken.

## Context

Every design doc up to this point (`2026-10-11-gmp-poller-design.md`, the
spec that originally shaped `gmp_source.py`) was written against IPO
Guru's **documentation pages**, never a real authenticated response — no
key existed yet. A real key arrived today (2026-10-25). Two real calls
confirmed the actual contract:

- `GET /api/v2/me` — confirms the key, plan (`free`), and real daily
  usage (`limits.per_minute: 1`, `limits.per_day: 10`,
  `limits.gmp_history_days: 0`). **Note for future manual testing**:
  `/me` counts against the same 10/day quota as every other call — do
  not call it defensively before each real request just to check
  remaining quota first, since that burns the budget faster than simply
  making the real call. The poller's own code already handles a 429
  gracefully (logs it, aborts that poll cleanly, writes zero snapshots),
  so there is no correctness reason to pre-check quota either in code or
  by hand. Reserve `/me` for when the usage/plan data is itself what is
  being asked for.
- `GET /api/v2/ipos` — a real 165-entry response, matching the field
  shapes this project already assumed.

The full `/api/v2` reference (fetched from IPO Guru's own reference page,
not the earlier marketing copy) confirms one load-bearing fact that
changes everything: **`/api/v1` is retired.** A call to it now returns a
`v1_retired` error. `gmp_source.py`'s `IPOS_URL` constant still points at
`/api/v1/ipos` — the live poller is broken right now, today, independent
of anything else in this doc.

## The one required code change

`ipo_watcher/gmp_source.py`'s `IPOS_URL`:

```python
IPOS_URL = "https://www.ipoguru.in/api/v1/ipos"   # retired, 404/v1_retired
```

becomes:

```python
IPOS_URL = "https://www.ipoguru.in/api/v2/ipos?months=0"
```

`months=0` is a new, previously-unknown query parameter on `/ipos` —
without it, the endpoint defaults to `months=3` and may silently exclude
IPOs filing more than three months out. Passing `months=0` removes that
limit at no extra cost (still one call, same envelope). This is the only
functional change this spec asks for — see "Explicitly out of scope"
below for everything this deliberately does not also change.

## What the real response confirms needs no change

Every field `parse_gmp_listing` currently reads matches the verified v2
contract exactly:

| What `gmp_source.py` reads | v2 reference says | Verified in the real response |
|---|---|---|
| `entry["name"]` | string, company name | yes |
| `entry["gmp"]["percentage"]` | string or null | yes, e.g. `"9.76"` |
| `entry["gmp"]["price"]` | string or null | yes, e.g. `"8"` |
| `entry["close_date"]` | `YYYY-MM-DD` or null | yes, e.g. `"2026-11-02"` |
| `entry["is_listed"]` | real boolean | yes, e.g. `false` |
| envelope shape | `{"success", "plan", "count", "data": [...]}` | yes, exactly |

`parse_gmp_listing`'s defensive parsing (added after the last review
round — `_parse_close_date`'s regex check, `_parse_is_listed`'s
bool/string handling, the NaN/infinite guard on `gmp_pct`) stays exactly
as it is. None of it was wrong; it was written defensively before real
data existed, and real data didn't surface anything it doesn't already
handle.

v2 adds roughly fifteen fields this project has no use for (`open_date`,
`allotment_date`, `stage`, `dates_tba`, `logo`, `price_band`,
`price_min`/`max`, `issue_price`, `issue_size`, `lot_size`,
`min_investment`, `subscription_total`, `listing_price`,
`listing_gain_percent`, `cmp`, `current_gain_percent`, `web_url`, and
three new fields inside `gmp` itself: `kostak`, `subject_to_sauda`,
`updated_at`/`updated_at_label`). `parse_gmp_listing` already ignores
unknown fields (confirmed by
`test_parse_gmp_listing_ignores_unknown_fields`) — no change needed to
tolerate any of these.

## Why `/ipos`, not `/gmp`

`/gmp` looks like the more obviously-named endpoint for a GMP-only
poller, and it is available on the free plan. But its documented response
shape is narrower than `/ipos`'s and **does not include `close_date` or
`is_listed`** — the two fields `send_gmp_update_alerts`'s stopping
condition depends on entirely. Switching to `/gmp` would silently break
the "stop once listed or past close date" behavior with no error to
signal it. `/ipos` remains the only correct endpoint for this project's
actual data needs, even though it returns more fields than needed —
recording this explicitly so a future "optimization" doesn't make this
exact mistake.

## Error handling: confirmed compatible, no change needed

The real v2 error taxonomy is richer than what the original poller
design assumed, but `fetch_gmp_listing`/`poll_gmp_snapshots`'s existing
status-code-based handling (any non-2xx → `raise_for_status()` →
`requests.HTTPError` → logged, poll aborts cleanly, zero snapshots
written) already covers every case correctly, since it branches on HTTP
status, not on the `error` field in the body:

| Status | `error` code | Already handled correctly? |
|---|---|---|
| 401 | `missing_key` | yes — generic `HTTPError` catch |
| 403 | `invalid_key` | yes — generic `HTTPError` catch |
| 402 | `plan_upgrade_required` | yes — generic `HTTPError` catch |
| 429 | `rate_limited` (burst, 1/min) | yes — generic `HTTPError` catch |
| 429 | `daily_quota_reached` (10/day) | yes — generic `HTTPError` catch |
| 422 | query validation failure | yes, and avoided by design — this project sends no query params that could trigger it (only `months=0`, not `status`/`type`, which are the ones with validated enums) |

No code change needed here. Recorded for the next person who touches
this file, so the richer error taxonomy doesn't look like a gap that
needs filling.

## Explicitly out of scope (deferred, not needed for this project)

- **Every field v2 adds** that this project doesn't consume (see table
  above) — no reason to parse or store data nothing reads.
- **Every endpoint beyond `/ipos`** — `/ipos/{slug}`, `/gmp`,
  `/ipos/{slug}/gmp`, `/ipos/{slug}/gmp/history` (Standard plan anyway),
  `/ipos/{slug}/subscription` (Standard), `/ipos/{slug}/anchors`
  (Standard), `/ofs`/`/unlisted` (Pro). This project only ever needed
  `GMP %`, `close_date`, and `is_listed` — all three come from the one
  `/ipos` list call already in use.
- **Logging `X-RateLimit-Remaining`/`X-Plan` response headers** — would
  give visibility into real-time quota usage, but is a monitoring
  nicety, not something the poller's own correctness depends on.
  Revisit only if quota exhaustion becomes a real operational problem.
- **Calling `/me` from the poller itself** — would cost a request against
  the same 10/day budget for a check that isn't required for the poller
  to function. Useful for manual debugging only (as done to verify this
  key), not for the automated pipeline.

## What this spec asks to be implemented next

1. Update `IPOS_URL` in `ipo_watcher/gmp_source.py` from the retired
   `/api/v1/ipos` to `https://www.ipoguru.in/api/v2/ipos?months=0`.
2. Update any test fixtures/comments referencing the old `/api/v1` URL to
   match.
3. Run the full test suite to confirm nothing else assumed the old URL
   string anywhere.

That's the complete scope — this is a one-line production fix with a
regression test, not new functionality.

## Open questions for the next pass

None outstanding. The real API confirmed every prior assumption except
the retired base URL and the undocumented `months` default, both
resolved above.
