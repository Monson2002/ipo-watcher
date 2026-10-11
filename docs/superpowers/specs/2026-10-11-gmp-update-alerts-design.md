# GMP Update Alerts — Design

> Design doc for a follow-up email when a tracked IPO's GMP moves
> meaningfully after the original filing alert. Resolves the "refresh
> cadence" question left open across the scoring-formula, gmp-snapshot-
> schema, and gmp-poller design docs. Formula refit cadence (the other
> half of that open question) is deferred separately — see "Explicitly
> out of scope."

## Context

`run.py`'s `send_pending_alert` computes a predicted Listing Gain once,
at the moment a filing (or corrigendum) is first emailed, from whatever
GMP % existed at that instant. GMP keeps moving for days afterward, right
up to the close of the bidding window — and the email never updates. This
design adds a second, independent alert: a follow-up email whenever a
tracked company's GMP moves far enough from what was last actually shown
to the user to be worth a fresh look, with extra emphasis on the final
day investors can still apply or revoke their application.

## The three agreed decisions

- **Movement threshold: ≥5 percentage points absolute**, measured against
  the GMP % that was last actually emailed for that company (not the
  last poll) — percentage points to match every other number in this
  project (MAE, the error-percentile interval), not a relative percent
  change.
- **No daily cap.** Every poll that crosses the threshold sends its own
  update and resets the baseline to that new value. A company that moves
  5pp, settles, then moves another 5pp later the same day gets two
  emails — intentional, not a bug to fix later.
- **"Last day" emphasis = `close_date`** (IPO Guru's field for "when
  bidding ends," i.e. the final day investors can still apply or revoke
  an application) — not `listing_date`. An update email for a company
  whose `close_date` is today gets an extra note calling that out.

## Architecture

Three new pieces of state, two new functions grouped with their existing
counterparts, **zero new cron entries** — this rides `gmp_source.py`'s
existing poll cycle (10 calls/day on the free tier), reading only
already-stored data, so it costs nothing extra against the API budget.

**`db.py`**:
- `gmp_snapshots` gains two nullable columns: `close_date TEXT`
  (IPO Guru's confirmed `close_date` field, `YYYY-MM-DD`) and
  `is_listed INTEGER` (IPO Guru's confirmed boolean `is_listed` field,
  stored as SQLite's usual 0/1). Both are recorded for **every** poll of
  **every** company, regardless of whether that company is still
  eligible for update emails (see "Stopping update emails" below) —
  this data keeps accumulating for later analysis even after a company
  stops generating emails (see `send_gmp_update_alerts`'s stopping
  condition in the `run.py` section below). This is a schema change to
  an already-shipped table — see "A note on schema changes" below.
- A new table, `gmp_notifications` (`id`, `company_id`, `gmp_pct`,
  `notified_at`) — **what GMP value was last actually emailed** to the
  user for this company, as distinct from `gmp_snapshots` (every poll,
  regardless of whether anyone was told about it). This is the
  comparison baseline the threshold check reads.
- New functions:
  - `record_gmp_notification(conn, company_id, gmp_pct)` — same shape as
    `record_gmp_snapshot`, no dedup key (every notification is its own
    row).
  - `get_last_notified_gmp_pct(conn, company_id) -> float | None`.
  - `get_notified_company_ids(conn) -> list[int]` — every company with at
    least one row in `gmp_notifications`; this is the eligible set for
    the update check (a company nobody's been told about yet has no
    baseline to compare against).
  - `get_latest_close_date(conn, company_id) -> str | None` — same
    `ORDER BY polled_at DESC, id DESC` pattern as `get_latest_gmp_pct`.
  - `get_latest_is_listed(conn, company_id) -> bool | None` — same
    pattern, returns `None` only when there's no snapshot at all yet
    (distinct from a real `False`).

**`gmp_source.py`**:
- `GmpRow` gains `close_date: str | None = None` and
  `is_listed: bool | None = None`.
- `parse_gmp_listing` reads `entry.get("close_date")` and
  `entry.get("is_listed")` alongside the existing `gmp`/`name` fields —
  absence is not an error (an "Upcoming" IPO before its bidding window
  opens may not have a `close_date` yet; IPO Guru's docs don't show
  `is_listed` ever being absent, but treat a missing key as `None` rather
  than assuming `False`, since "unknown" and "confirmed not listed" are
  different claims).
- `poll_gmp_snapshots` passes `row.close_date`/`row.is_listed` through to
  `record_gmp_snapshot`.
- `main()` calls `run.send_gmp_update_alerts(conn)` immediately after
  `poll_gmp_snapshots(conn)`, inside the same `try`/`finally` connection
  lifecycle — one cron invocation now does both jobs.

**`run.py`**:
- `send_pending_alert` calls `db.record_gmp_notification(conn,
  row["company_id"], gmp_pct)` whenever the computed prediction is real
  (not the `"GMP not yet available."` fallback) — this is what
  establishes the baseline the first time, piggybacking on the alert
  that already fires for a new filing.
- New `send_gmp_update_alerts(conn)`:
  1. For each `company_id` in `db.get_notified_company_ids(conn)`:
     fetch `is_listed = db.get_latest_is_listed(conn, company_id)` and
     `close_date = db.get_latest_close_date(conn, company_id)`.
  2. **Stopping condition**: if `is_listed` is `True`, or `close_date` is
     set and has already passed (today's IST date > `close_date`), skip
     this company entirely — no further update emails, ever, for this
     company. `gmp_snapshots` keeps recording it regardless (that
     happens unconditionally in `poll_gmp_snapshots`, upstream of this
     function); only the email-eligibility check stops.
  3. Otherwise, fetch `latest_pct = db.get_latest_gmp_pct(conn,
     company_id)` and `last_notified =
     db.get_last_notified_gmp_pct(conn, company_id)`. If `latest_pct is
     None` or `abs(latest_pct - last_notified) < 5.0`: skip this company
     for this poll (not a permanent stop — it may still cross the
     threshold on a later poll, same as before).
  4. Otherwise, build an update row: company name, `last_notified`,
     `latest_pct`, `scoring.format_prediction(scoring.predict_listing_gain(latest_pct))`,
     and a `closing_today` flag (`close_date == today's IST date`).
  5. Call `db.record_gmp_notification(conn, company_id, latest_pct)` to
     reset the baseline — happens whether or not the email send
     ultimately succeeds, same as every other "new state exists once
     polled" decision in this project (an email failure here just means
     the next real move still gets its own fresh comparison, not a
     retry of this exact one).
  6. If any rows were built, call
     `notifier.send_gmp_update_alert(rows)` once (batched, matching
     `send_pending_alert`'s existing one-email-per-run convention).

**`notifier.py`**:
- Extract `_send_email(subject, body)` out of the existing `send_alert` —
  pure refactor (SMTP connect/login/send, try/except, return
  `True`/`False`), no behavior change, so `send_alert`'s existing tests
  must keep passing unmodified.
- `send_alert(rows)` becomes: build subject/body as today, then call
  `_send_email`.
- New `build_gmp_update_subject(rows)`, `build_gmp_update_body(rows)`,
  `send_gmp_update_alert(rows)` (same `if not rows: return True` short
  circuit as `send_alert`, then build + `_send_email`).
- Body format per company:
  ```
  Acme Robotics Ltd: GMP moved from 12.0% to 18.5%
  Predicted Listing Gain: +18.9%
  Historically, actual outcomes for a similar prediction have ranged from
  about -3% to +48% (5th-95th percentile of past prediction errors) - the
  spread is NOT symmetric; big positive surprises are more common than
  big negative ones.
  Based on GMP % (grey market premium - an unofficial, informal
  indicator; not exchange-verified). This is a statistical estimate, not
  advice.
  Bidding closes today - this is the last day to apply or revoke an
  application.
  ```
  The last line appears only when `closing_today` is true.

## A note on schema changes

`gmp_snapshots` already shipped (it's in `main` now). Adding a column to
an existing `CREATE TABLE IF NOT EXISTS` string does nothing for a
database file that already has the table — `IF NOT EXISTS` skips the
whole statement. This project has no migrations framework by design
(see `CLAUDE.md`), and nothing is deployed to a real server yet (also
per `CLAUDE.md` — deployment is still a manual, not-yet-done step), so
the only real `gmp_snapshots` tables in existence right now are
ephemeral (`:memory:` in tests, or a local dev file). Given that, this
spec treats the column addition as safe *today*. **This stops being
true the moment a real `data/ipo_watcher.db` exists on a deployed
server** — at that point, adding `close_date` would need an explicit
`ALTER TABLE gmp_snapshots ADD COLUMN close_date TEXT` run once by hand,
not a schema-string edit. Flagging this now so it isn't a surprise
later; not solving it now, since deployment itself hasn't happened yet.

## Error handling

- A company with `latest_pct is None` (no snapshot yet, or the one
  poll that existed got pruned — can't happen today, but cheap to guard)
  is skipped, not an error.
- `send_gmp_update_alerts` runs after `poll_gmp_snapshots` in the same
  `main()`; if `poll_gmp_snapshots` itself aborted cleanly on a `429`/
  other HTTP error (see the poller design doc), there's no new data, so
  every company's `latest_pct` is unchanged from last time and nothing
  crosses the threshold — the update check is naturally a no-op that
  poll, not a special case to handle.
- `notifier.send_gmp_update_alert` returning `False` (SMTP failure) does
  **not** roll back the `record_gmp_notification` baseline reset (see
  step 4 above) — matches this project's existing stance that losing one
  email is acceptable when the alternative is new machinery to retry a
  specific historical comparison.

## Testing

- `tests/test_db.py`: round-trip test for `close_date` on
  `gmp_snapshots`; `record_gmp_notification`/`get_last_notified_gmp_pct`
  round-trip; `get_notified_company_ids` returns the right set across
  multiple companies; `get_latest_close_date` mirrors
  `get_latest_gmp_pct`'s existing tie-breaking/scoping tests.
- `tests/test_gmp_source.py`: `parse_gmp_listing` reads `close_date` when
  present, defaults to `None` when absent; `poll_gmp_snapshots` passes it
  through.
- `tests/test_notifier.py`: `_send_email` extraction doesn't change
  `send_alert`'s existing test outcomes; new tests for
  `build_gmp_update_body` with and without the closing-today line.
- `tests/test_run.py`: `send_gmp_update_alerts` — below threshold (no
  email), at/above threshold (email sent, baseline reset), multiple
  companies moving in one poll (one batched email), a company with no
  notification history yet (skipped, not an error), a company whose
  `is_listed` is `True` (skipped even though its GMP moved far enough),
  a company whose `close_date` is in the past (same), a company whose
  `close_date` is exactly today (included, with the closing-today note).

## Explicitly out of scope (deferred to later, separate design passes)

- **Formula refit cadence** (retraining `scoring.py`'s coefficients) —
  the other half of the original "refresh/refit cadence" question. Stays
  a documented manual-review policy, not code; revisit once a real IPO
  Guru API key exists and some real usage has happened (user's own
  timeline: next week).
- **Any cap/debounce on update emails** — explicitly rejected in this
  design per the "no daily cap" decision above; revisit only if real
  usage shows it's actually noisy.
- **The migration problem** flagged in "A note on schema changes" —
  solving it now would be solving a problem that doesn't exist yet
  (nothing is deployed).

## Open questions — resolved, 2026-10-11

1. ~~Should a company ever "graduate out" of update checking?~~ —
   **resolved**: yes. A company stops receiving update emails once its
   `close_date` has passed or `is_listed` becomes `True`, whichever comes
   first. `gmp_snapshots` keeps recording that company on every poll
   regardless — the stop applies only to `send_gmp_update_alerts`'s
   email-eligibility check, never to storage. See the "Stopping
   condition" step in `send_gmp_update_alerts` above.
2. ~~Should `is_listed` factor in at all?~~ — **resolved**: yes, folded
   into the same stopping condition as `close_date` above, via the new
   `get_latest_is_listed` accessor and the `is_listed` column on
   `gmp_snapshots`.
