# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git

`git add` is as far as Claude goes in this repo. Never run `git commit` or `git push` — stage changes and stop there; the user commits and pushes themselves.

## Journal

`JOURNAL.md` is a day-by-day, first-person project log.

## Commands

This project uses `uv`, not `pip`/`requirements.txt` — never add or restore a `requirements.txt`.

```bash
uv sync                              # install/update the venv from pyproject.toml + uv.lock
uv run python -m ipo_watcher.run     # run the collector once
uv run pytest tests/ -v              # full test suite
uv run pytest tests/test_run.py -v   # one file
uv run pytest tests/test_run.py::test_discover_new_filings_skips_detail_fetch_for_known_rows -v  # one test
```

No lint/typecheck/build step is configured. `pyproject.toml`'s `[tool.pytest.ini_options] pythonpath = ["."]` is required for `ipo_watcher` to be importable under test — the package is intentionally not installed into the venv (`uv.lock` marks it `virtual`), so don't "fix" this by adding a `[build-system]` unless you actually need the package installed elsewhere.

## Architecture

A single flat `ipo_watcher` package. Eight modules, two pipelines: the original **discover → download → alert** collector, orchestrated by `run.py`'s three top-level functions (`discover_new_filings`, `download_pending_pdfs`, `send_pending_alert`), each independently idempotent and safe to re-run — plus a second, separate **poll → store → read-back → re-alert** pipeline for live GMP data (`gmp_source.py`'s own cron entry calls `poll_gmp_snapshots` then `run.send_gmp_update_alerts`; `send_pending_alert` separately reads the result back out at send time via `scoring.py`).

- **`sebi_source.py`** — fetches and parses SEBI's Public Issues filing listing (`HomeAction.do?doListing=yes&sid=3&ssid=15&smid=-1` — `smid=-1` is load-bearing, it means "all filing sub-types combined"; a specific `smid` value would silently miss RHP/Prospectus/Addenda). Page 1 is a plain GET; page ≥2 is a POST to a separate AJAX endpoint (`getnewslistinfo.jsp`) with a specific field set and a `nextValue`/`doDirect` = `page_no - 1` formula — both endpoints were confirmed by live requests during design, not guessed, so don't "simplify" the pagination math without re-verifying against the real site. Row parsing uses a hand-rolled regex, not BeautifulSoup: SEBI's HTML has an invalid nested `<a>` inside `<a>` on some rows (e.g. a DRHP row that also directly links its Abridged Prospectus), and the regex + "split captured text on the first literal `<`" trick handles this deliberately — see the comments in `parse_listing`/`ROW_RE` before changing this. A filing's *real* PDF (the actual DRHP/RHP) is only found by fetching its detail page and reading an `<iframe src='.../web/?file=URL'>` — the detail page separately links a shorter "Abridged Prospectus" PDF elsewhere on the page, which is deliberately ignored (out of scope, see the plan doc).
- **`db.py`** — SQLite schema (`companies`, `filings`, `gmp_snapshots`, `gmp_notifications`) and all queries. `filings.dedup_key` (sha256 of company+type+date+url) is the entire idempotency mechanism via `INSERT OR IGNORE` — a corrigendum/addendum is just a new row with the same `company_id`, never a schema change. `notified_at IS NULL` doubles as "needs emailing" and the crash-retry condition (a failed send never marks rows notified, so the next run resends the whole batch). `pdf_path IS NULL` is the equivalent condition for pending downloads. `gmp_snapshots` is every poll of every tracked company (no dedup key, deliberately — every poll is its own row); `gmp_notifications` is a separate, smaller history of what GMP value was last actually emailed per company — the baseline `send_gmp_update_alerts` compares against. Neither table has a migrations path: both were added as direct edits to the `SCHEMA` string, safe only because nothing is deployed to a real server yet (see `docs/superpowers/specs/2026-10-11-gmp-update-alerts-design.md`'s "A note on schema changes").
- **`downloader.py`** — downloads to a `.tmp` path, hashes while streaming, `os.replace()`s into place only on full success (crash-safe; never leaves a half-written PDF at the real filename).
- **`notifier.py`** — one batched plain-text email per run via stdlib `smtplib`, only when there's something new. Returns `True`/`False` rather than raising; `run.py` only calls `db.mark_notified` on `True`.
- **`run.py`** — wires the above together. `discover_new_filings` short-circuits on `source_page_url` already present in the DB *before* fetching a filing's detail page (steady-state runs would otherwise re-fetch every known filing's detail page every time), and stops paginating once a page yields either zero rows or zero newly-inserted filings. A `REQUEST_DELAY_SECONDS` courtesy sleep sits between outbound requests — SEBI is a government site with no documented rate limit, don't remove this. All timestamps (`config.IST`, `zoneinfo.ZoneInfo("Asia/Kolkata")`) are recorded in IST regardless of the host machine's local timezone — both DB timestamps (`db._now_iso`) and log lines (`run._ISTFormatter`) go through this; don't reintroduce naive `datetime.now()`/UTC. `send_pending_alert` enriches each row with a `gmp_prediction` string before handing it to `notifier`, and records a `gmp_notifications` baseline (`db.record_gmp_notification`) — but only once the send actually succeeds, matching this function's existing retry contract. `send_gmp_update_alerts` (called from `gmp_source.py`'s cron, not `run.py`'s own) compares each tracked company's latest polled `GMP %` against that baseline and sends a follow-up email on a ≥5pp absolute move, permanently skipping a company once `is_listed` or `close_date` says its window is over (re-checked every poll, not latched — see the update-alerts design doc's "Open questions" for why).
- **`gmp_source.py`** — fetches live Grey Market Premium data from IPO Guru's free API (`fetch_gmp_listing`), parses it (`parse_gmp_listing`), matches each entry to a known `companies` row by normalized name (`match_company` — exact match only, never creates a company row, deterministic lowest-id tiebreak on a normalization collision), and stores a snapshot (`poll_gmp_snapshots` → `db.record_gmp_snapshot`, including `close_date`/`is_listed`). Runs on its **own, separate, more frequent cron entry** (`python -m ipo_watcher.gmp_source`, see `README.md`) — not folded into `run.py`'s cycle, since the free API tier allows only 10 requests/day; fails fast (no network call) if `IPO_GURU_API_KEY` is empty. A `429`/other HTTP error aborts the poll cleanly (logged, zero snapshots written); only connection/timeout errors retry.
- **`scoring.py`** — the frozen Listing Gain formula (`predict_listing_gain`, `format_prediction`), fit on historical data in `research/ipo-scoring-validation/` and moved here once `run.py` actually wired it in. Self-contained, no DB/network dependency — `run.py` supplies the live `GMP %` via `db.get_latest_gmp_pct`.

**Scope note**: IPO scoring/recommendation and live GMP data are no longer out of scope — both are implemented (`gmp_source.py`, `scoring.py`, wired into the email). What's still explicitly out of scope: PDF financial-metric extraction, and any "top picks"/ranking logic — the email lists every new filing, each with its own GMP-based prediction when available, never a ranked subset.

**Config** is env vars only (`ipo_watcher/config.py`, loaded via `.env` + `python-dotenv`) — SMTP credentials, `ALERT_FROM`/`ALERT_TO`, `IPO_GURU_API_KEY`, and optional `IPO_WATCHER_DB_PATH`/`PDF_DIR`/`LOG_PATH` overrides. No cloud services anywhere in this project — SQLite + local disk + cron only, by design (see the plan doc's Global Constraints). Deployment is not automated: both cron lines (filing collector, GMP poller) live in `README.md`, meant to be installed by hand on whatever server ends up hosting this.

## Design history

The original design/build plan (including the live-confirmed SEBI endpoint details referenced above) is at `docs/superpowers/plans/2026-09-06-sebi-ipo-collector.md` — read it before making structural changes to `sebi_source.py`'s fetch/parse logic, since several of its choices look arbitrary without the "confirmed facts" context it documents.

The GMP/scoring pipeline has its own design trail, each doc built on the last: `docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md` (the formula itself — why GMP % alone, not fundamentals), `docs/superpowers/specs/2026-10-11-gmp-snapshot-schema-design.md` (the `gmp_snapshots` table), `docs/superpowers/specs/2026-10-11-gmp-poller-design.md` (the live poller, and the real IPO Guru free-tier numbers — 10 req/day, not 300 as an earlier draft wrongly assumed), and `docs/superpowers/specs/2026-10-11-gmp-update-alerts-design.md` (the follow-up "GMP moved" email, its 5pp threshold, and why the stopping condition isn't actually latched). Read these before changing `gmp_source.py`/`scoring.py`/`send_gmp_update_alerts` — several choices (no fuzzy name matching, a separate cron instead of folding into `run.py`'s cycle, an asymmetric empirical interval instead of a flat ±MAE band, no daily cap on update emails) were deliberate, not oversights.
