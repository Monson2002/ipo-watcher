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
