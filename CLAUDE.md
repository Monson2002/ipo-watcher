# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

A single flat `ipo_watcher` package. Six modules, one pipeline: **discover → download → alert**, orchestrated by `run.py`'s three top-level functions (`discover_new_filings`, `download_pending_pdfs`, `send_pending_alert`), each independently idempotent and safe to re-run.

- **`sebi_source.py`** — fetches and parses SEBI's Public Issues filing listing (`HomeAction.do?doListing=yes&sid=3&ssid=15&smid=-1` — `smid=-1` is load-bearing, it means "all filing sub-types combined"; a specific `smid` value would silently miss RHP/Prospectus/Addenda). Page 1 is a plain GET; page ≥2 is a POST to a separate AJAX endpoint (`getnewslistinfo.jsp`) with a specific field set and a `nextValue`/`doDirect` = `page_no - 1` formula — both endpoints were confirmed by live requests during design, not guessed, so don't "simplify" the pagination math without re-verifying against the real site. Row parsing uses a hand-rolled regex, not BeautifulSoup: SEBI's HTML has an invalid nested `<a>` inside `<a>` on some rows (e.g. a DRHP row that also directly links its Abridged Prospectus), and the regex + "split captured text on the first literal `<`" trick handles this deliberately — see the comments in `parse_listing`/`ROW_RE` before changing this. A filing's *real* PDF (the actual DRHP/RHP) is only found by fetching its detail page and reading an `<iframe src='.../web/?file=URL'>` — the detail page separately links a shorter "Abridged Prospectus" PDF elsewhere on the page, which is deliberately ignored (out of scope, see the plan doc).
- **`db.py`** — SQLite schema (`companies`, `filings`) and all queries. `filings.dedup_key` (sha256 of company+type+date+url) is the entire idempotency mechanism via `INSERT OR IGNORE` — a corrigendum/addendum is just a new row with the same `company_id`, never a schema change. `notified_at IS NULL` doubles as "needs emailing" and the crash-retry condition (a failed send never marks rows notified, so the next run resends the whole batch). `pdf_path IS NULL` is the equivalent condition for pending downloads.
- **`downloader.py`** — downloads to a `.tmp` path, hashes while streaming, `os.replace()`s into place only on full success (crash-safe; never leaves a half-written PDF at the real filename).
- **`notifier.py`** — one batched plain-text email per run via stdlib `smtplib`, only when there's something new. Returns `True`/`False` rather than raising; `run.py` only calls `db.mark_notified` on `True`.
- **`run.py`** — wires the above together. `discover_new_filings` short-circuits on `source_page_url` already present in the DB *before* fetching a filing's detail page (steady-state runs would otherwise re-fetch every known filing's detail page every time), and stops paginating once a page yields either zero rows or zero newly-inserted filings. A `REQUEST_DELAY_SECONDS` courtesy sleep sits between outbound requests — SEBI is a government site with no documented rate limit, don't remove this. All timestamps (`config.IST`, `zoneinfo.ZoneInfo("Asia/Kolkata")`) are recorded in IST regardless of the host machine's local timezone — both DB timestamps (`db._now_iso`) and log lines (`run._ISTFormatter`) go through this; don't reintroduce naive `datetime.now()`/UTC.

**Explicitly out of scope** (don't add speculatively): PDF financial-metric extraction, IPO scoring/recommendation, GMP or live market-price data. The email alert lists *every* new filing found, unranked — there is no "top picks" logic, and none should be added without a real design pass (financial extraction + scoring rules don't exist yet).

**Config** is env vars only (`ipo_watcher/config.py`, loaded via `.env` + `python-dotenv`) — SMTP credentials, `ALERT_FROM`/`ALERT_TO`, and optional `IPO_WATCHER_DB_PATH`/`PDF_DIR`/`LOG_PATH` overrides. No cloud services anywhere in this project — SQLite + local disk + cron only, by design (see the plan doc's Global Constraints). Deployment is not automated: the cron line lives in `README.md`, meant to be installed by hand on whatever server ends up hosting this.

## Design history

The original design/build plan (including the live-confirmed SEBI endpoint details referenced above) is at `docs/superpowers/plans/2026-09-06-sebi-ipo-collector.md` — read it before making structural changes to `sebi_source.py`'s fetch/parse logic, since several of its choices look arbitrary without the "confirmed facts" context it documents.
