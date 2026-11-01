# ipo-watcher

Watches SEBI's Public Issues filings (DRHP/RHP/Prospectus/Addenda/Corrigenda)
and emails an alert when a new one appears.

## Setup

    uv sync
    cp .env.example .env
    # edit .env with your Resend API key (see below)

Email is sent via [Resend](https://resend.com)'s HTTP API, not SMTP. Sign up
for a Resend account using the same address as `ALERT_TO` (this project's
alert email has exactly one recipient), generate an API key, and set
`RESEND_API_KEY` in `.env`. Leave `ALERT_FROM` blank to use Resend's
`onboarding@resend.dev` test address — without a verified domain, that
address can only deliver to the email the Resend account itself was signed
up with, which is exactly `ALERT_TO` here.

## Run once

    uv run python -m ipo_watcher.run

Check `logs/ipo_watcher.log` for what happened, `data/ipo_watcher.db` for
stored filings, and `data/pdfs/` for downloaded documents.

The very first run against an empty database will discover SEBI's entire
recent Public Issues filing history (potentially hundreds of filings and
several GB of PDFs) and may take a while. Subsequent runs are fast since
only new filings are processed.

## Deployment: GitHub Actions (live)

This project runs on two scheduled GitHub Actions workflows —
`.github/workflows/sebi-collector.yml` and `.github/workflows/gmp-poller.yml`
— not a manually-installed cron job. Each run checks out the repo, runs the
relevant module, and commits+pushes `data/ipo_watcher.db` back if it
changed, so the database persists between ephemeral runner instances. Both
workflows share one concurrency group (`ipo-watcher-db-write`) so they never
race on that commit.

- **SEBI collector** — runs Mon/Wed/Fri at 9:00 AM IST (`30 3 * * 1,3,5` UTC
  in the workflow file), with `--skip-pdf-download` (ephemeral runners can't
  persist PDFs — run locally if you need the actual files).
- **GMP poller** — runs 10 times/weekday across market hours, respecting IPO
  Guru's free-tier 10 requests/day cap; see the workflow file for the exact
  times.

Required repo secrets: `RESEND_API_KEY`, `ALERT_FROM`, `ALERT_TO` (both
workflows), plus `IPO_GURU_API_KEY` (poller only). Trigger either workflow
manually via `workflow_dispatch` in the Actions tab to test without waiting
for the schedule.

Design/history: `docs/superpowers/specs/2026-10-25-github-actions-deployment-design.md`.
