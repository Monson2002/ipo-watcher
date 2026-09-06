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

Add (checks a few times a day during business hours — SEBI filings aren't
high-frequency, no need to poll hourly):

    0 8,10,12,14,16,18,20 * * * cd /path/to/ipo-watcher && /path/to/.venv/bin/python -m ipo_watcher.run >> logs/cron.log 2>&1
