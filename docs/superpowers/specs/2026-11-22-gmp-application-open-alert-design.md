# GMP Application-Open Alert — Design

> Bounded addition to the existing GMP poll/alert pipeline, approved in
> chat on 2026-11-22. Written down here only because this subsystem's
> other features (the poller, the update alerts, the v2 migration) all
> have a doc trail in this directory — not an architectural-path spec.
>
> Earlier the same day a different feature was drafted under this same
> filename stem ("listing-day alert" — fires when a stock actually
> lists on the exchange). That was a misunderstanding of the ask and
> was reverted; this doc replaces it under a new name. If the
> listing-day idea is wanted later, it needs its own fresh design.
>
> **Amended 2026-11-22, post-implementation review:** two reviews of the
> implementation (one independent subagent, one manual re-check) found
> the design below buggy on two points, both now fixed in code before
> merge — this doc is updated to describe the corrected design, not the
> original draft. See `docs/superpowers/plans/2026-11-22-gmp-application-open-alert.md`
> and its ledger (`.superpowers/sdd/2026-11-22-gmp-application-open-alert/progress.md`)
> for the full history of what was wrong and why.

## Context

A SEBI filing (DRHP/RHP) gets discovered and alerted as soon as it's
published, and `send_gmp_update_alerts` sends "GMP now available" the
moment IPO Guru starts reporting a GMP% for that company — both of
which can happen days before the IPO's actual bidding/application
window opens (confirmed live: Crystal Crop Protection's RHP was
notified and its first GMP email sent on 2026-11-13, but its
application window didn't open until 2026-11-18 — a gap that read as a
bug until the user explained the timing). The ask: a distinct, one-time
email on the actual day applications open, carrying the GMP% at that
moment, so "can I apply today" has a direct answer instead of being
inferred from filing/GMP timestamps.

## New data captured

IPO Guru's v2 `/ipos` response already includes an `open_date` field
per entry (confirmed in `2026-10-25-ipo-guru-v2-migration-design.md`'s
field table, currently discarded as out-of-scope, the same way
`listing_price`/`listing_gain_percent` were before this project started
using them). This feature consumes it:

- `GmpRow` gains `open_date: str | None = None`.
- `parse_gmp_listing` parses it the same defensive way as the existing
  `close_date` (`_CLOSE_DATE_RE`-shaped string or `None`).
- `gmp_snapshots` gains one new nullable column, `open_date TEXT`.
  **This table is already deployed** (GitHub Actions went live
  2026-10-25), so a direct `SCHEMA` edit alone is *not* safe here — a
  `CREATE TABLE IF NOT EXISTS` is a no-op against an existing table, and
  the real production `gmp_snapshots` was confirmed missing `open_date`
  during review, which would have crashed every poll on the first
  `INSERT`. `db.init_db` now also calls a new
  `db._ensure_column(conn, "gmp_snapshots", "open_date", "TEXT")` —
  an idempotent `PRAGMA table_info` check + `ALTER TABLE` — right after
  `executescript(SCHEMA)`. This table's original "no migration path"
  design note (see its own design doc) was accurate when written but is
  now out of date for any *existing* table; a brand-new table (like
  `application_open_notifications` below) can still rely on
  `CREATE TABLE IF NOT EXISTS` alone.
- `db.record_gmp_snapshot` takes one new optional kwarg for it.

**Caveat, stated up front:** this can only fire for a company IPO Guru
is already reporting GMP for. `parse_gmp_listing` only yields a row at
all when a GMP percentage is present — if GMP data doesn't exist yet
by the time the window opens (unusual, but possible for an IPO Guru
lags on), there is nothing to alert on that day. No code can work
around a gap in the upstream data; this is a known, accepted limit.

## Detection

**The original design here was wrong and never actually fired in the
realistic case — recorded for the next person who touches this code.**
The first draft dedup'd by checking whether any *existing* `gmp_snapshots`
row already carried this `open_date`. That fails because IPO Guru
typically reports `open_date` days *before* the window opens — in the
Crystal Crop Protection case above, `open_date` would have been known
from the very first GMP snapshot on 2026-11-13, five days before the
real trigger day. By the time "today" actually equals `open_date`, a
snapshot already carries that value (recorded on every earlier poll
too, since `open_date` doesn't change), so the "already recorded"
check is already true and the alert never fires. Reproduced live during
review.

The corrected design dedupes on whether the alert was actually **sent**,
not on anything stored in `gmp_snapshots`:

```
today = _today()  # gmp_source.py's own helper, datetime.datetime.now(config.IST).date().isoformat()
if row.open_date == today and company_id not in opens_today_company_ids and not db.has_notified_application_open(conn, company_id, row.open_date):
    opens_today.append({"company_id": company_id, "company_name": row.company_name, "gmp_pct": row.gmp_pct, "open_date": row.open_date})
    opens_today_company_ids.add(company_id)
```

- A new table, `application_open_notifications` (`company_id`,
  `open_date`, `notified_at`, `UNIQUE(company_id, open_date)`), records
  one row per company **only after** `run.send_application_open_alerts`
  succeeds — in `gmp_source.main()`, not inside `poll_gmp_snapshots`
  itself. This matches the retry contract every other alert in this
  pipeline already follows (`send_pending_alert`/`send_gmp_update_alerts`
  only persist "sent" state after a successful send) — a failed send
  leaves nothing recorded, so the next poll retries instead of losing
  the alert permanently.
- `opens_today_company_ids` is a plain per-poll `set`, needed because the
  notifications table is only updated *after* the whole poll finishes —
  two entries for the same company within one API response (observed as
  possible, not confirmed as something IPO Guru actually sends) would
  otherwise both pass the "not yet notified" check and produce a
  duplicated line in the same email.

## Delivery

`notifier.py` gets a fifth builder trio, matching the existing
`build_gmp_update_*`/pattern: `build_application_open_subject(rows)`,
`build_application_open_body(rows)`, `build_application_open_html(rows)`,
and `send_application_open_alert(rows)` (True/False contract, one
batched email for however many companies' windows opened in a single
poll). Each row: `company_name`, `gmp_pct` (the GMP% at the moment the
window opened). Actual body line (plain text, matching this file's two
existing builder trios rather than introducing new emoji/punctuation
conventions): "Acme Robotics Ltd opens for bidding today - GMP at
18.4%".

`run.py` gets a thin `send_application_open_alerts(rows)` wrapper that
just calls `notifier.send_application_open_alert(rows)` — kept in
`run.py` rather than called directly from `gmp_source.py` so
`gmp_source.py` stays pure fetch/store and `run.py` stays the alert
orchestrator, matching this pipeline's existing layering
(`gmp_source.main()` already calls `run.send_gmp_update_alerts`, not
`notifier` directly).

`poll_gmp_snapshots`'s result dict gains an `"opens_today"` key (list
of row dicts, empty list when none opened this poll).
`gmp_source.main()` calls `run.send_application_open_alerts` on that
list right after the poll, alongside the existing
`run.send_gmp_update_alerts` call — no overlap risk: a company whose
window opens today can also have crossed the 5pp GMP-movement
threshold on the same poll, and both emails are allowed to fire
independently since they answer different questions ("did GMP move"
vs. "can I apply today").

## Testing

- `test_gmp_source.py`: the dedup cases (first poll with `open_date ==
  today` fires; a later same-day poll for the same company does not
  re-fire once notified; a company whose `open_date` isn't today is
  never flagged; `open_date` parsing — present, missing, malformed) —
  plus the regression that actually pins the Detection fix: an earlier
  poll that stored a snapshot with tomorrow's `open_date` must not
  block the real trigger day once "today" catches up to it, and two
  entries for the same company in one poll response must not duplicate
  the email.
- `test_db.py`: the `gmp_snapshots.open_date` column round-trips through
  `record_gmp_snapshot`, including against a DB built with the
  pre-`open_date` schema (`_ensure_column` migration path);
  `has_notified_application_open`/`record_application_open_notification`
  round-trip correctly and don't leak between companies or open_dates,
  and are unaffected by unrelated `gmp_snapshots` rows carrying the same
  `open_date` value.
- `test_notifier.py`: the four new builders.
- `test_run.py`: `send_application_open_alerts` wrapper forwards to
  `notifier.send_application_open_alert` and returns its result.
- `test_gmp_source.py` (`main()`): recording only happens after a
  successful send — a failed `send_application_open_alerts` call must
  leave `has_notified_application_open` `False` so the next poll
  retries.
