# ipo-watcher

Watches SEBI's Public Issues filings (DRHP/RHP/Prospectus/Addenda/Corrigenda)
and emails an alert when a new one appears.

## Setup

    uv sync
    cp .env.example .env
    # edit .env with your SMTP credentials (a Gmail app password works)

## Run once

    uv run python -m ipo_watcher.run

Check `logs/ipo_watcher.log` for what happened, `data/ipo_watcher.db` for
stored filings, and `data/pdfs/` for downloaded documents.

The very first run against an empty database will discover SEBI's entire
recent Public Issues filing history (potentially hundreds of filings and
several GB of PDFs) and may take a while. Subsequent runs are fast since
only new filings are processed.

## Schedule with cron

    crontab -e

Add (runs Mon/Wed/Fri at 9:00 AM IST — set `CRON_TZ` if the machine running
cron isn't already on IST; not all cron implementations honor `CRON_TZ`, so
adjust the hour to your box's local-time equivalent of 9 AM IST if not):

    CRON_TZ=Asia/Kolkata
    0 9 * * 1,3,5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.run >> logs/cron.log 2>&1

(Cron day-of-week: 1=Mon, 3=Wed, 5=Fri.)

This isn't installed anywhere yet — install it on whichever server actually
hosts this long-term (not a throwaway/dev session), once deployed.

### GMP poller (separate, more frequent cron)

The GMP poller (`ipo_watcher.gmp_source`) is a **separate** cron entry from
the filing collector above — different cadence, different failure domain.
IPO Guru's free API tier allows only 10 requests/day, so this runs exactly
10 times, 45 minutes apart, across market hours — using the entire daily
budget with no headroom for ad-hoc calls that day.

A single `*/45 9-15 * * 1-5` line does **not** do this — cron's minute and
hour fields combine as a cartesian product, so that pattern fires at both
`:00` and `:45` every hour (14 runs/day, not 10), which would burn through
the budget by mid-afternoon and get 429s on the last few polls of the day
— exactly the readings that matter most as an IPO nears its listing date.
Ten separate lines, one per exact time, are needed instead:

    CRON_TZ=Asia/Kolkata
    0 9 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    45 9 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    30 10 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    15 11 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    0 12 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    45 12 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    30 13 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    15 14 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    0 15 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1
    45 15 * * 1-5 cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.gmp_source >> logs/gmp_cron.log 2>&1

That's 9:00, 9:45, 10:30, 11:15, 12:00, 12:45, 13:30, 14:15, 15:00, 15:45
— ten calls, each 45 minutes apart, none skipped.

Requires `IPO_GURU_API_KEY` set in `.env` (see `.env.example`). Like the
filing collector's cron line, this isn't installed anywhere yet — install
it by hand on whichever server actually hosts this long-term.
