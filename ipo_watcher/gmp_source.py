import logging
import os
import re
import time
from dataclasses import dataclass

import requests

from . import config, db

log = logging.getLogger("ipo_watcher.gmp_source")

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3
IPOS_URL = "https://www.ipoguru.in/api/v1/ipos"

_SUFFIX_RE = re.compile(r"\b(ltd|limited|pvt|private|ipo)\b\.?", re.IGNORECASE)


@dataclass
class GmpRow:
    company_name: str
    gmp_pct: float
    gmp_rupees: float | None = None
    close_date: str | None = None
    is_listed: bool | None = None


def parse_gmp_listing(response_json):
    rows = []
    for entry in response_json.get("data") or []:
        company_name = entry.get("name")
        if not company_name:
            continue
        gmp = entry.get("gmp")
        if not gmp:
            continue
        try:
            gmp_pct = float(gmp["percentage"])
        except (KeyError, TypeError, ValueError):
            continue
        gmp_rupees = None
        if gmp.get("price") is not None:
            try:
                gmp_rupees = float(gmp["price"])
            except (TypeError, ValueError):
                gmp_rupees = None
        rows.append(GmpRow(
            company_name=company_name,
            gmp_pct=gmp_pct,
            gmp_rupees=gmp_rupees,
            close_date=entry.get("close_date"),
            is_listed=entry.get("is_listed"),
        ))
    return rows


def _normalize_name(name):
    name = name.strip().lower()
    name = _SUFFIX_RE.sub("", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return name.strip()


def match_company(conn, raw_name):
    target = _normalize_name(raw_name)
    for row in conn.execute("SELECT id, name FROM companies"):
        if _normalize_name(row["name"]) == target:
            return row["id"]
    return None


def fetch_gmp_listing():
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                IPOS_URL,
                headers={"User-Agent": USER_AGENT, "X-API-KEY": config.IPO_GURU_API_KEY},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
            continue
        resp.raise_for_status()
        return resp.json()
    raise last_exc


def poll_gmp_snapshots(conn):
    try:
        response_json = fetch_gmp_listing()
    except requests.HTTPError as exc:
        log.error("GMP poll aborted: HTTP error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0}
    except requests.RequestException as exc:
        log.error("GMP poll aborted: network error %s", exc)
        return {"matched": 0, "unmatched": 0, "skipped_errors": 0}

    rows = parse_gmp_listing(response_json)
    total_entries = len(response_json.get("data") or [])
    skipped_errors = total_entries - len(rows)
    matched = unmatched = 0
    for row in rows:
        company_id = match_company(conn, row.company_name)
        if company_id is None:
            unmatched += 1
            log.warning("No company match for GMP entry %r", row.company_name)
            continue
        db.record_gmp_snapshot(
            conn, company_id, row.gmp_pct, row.gmp_rupees,
            close_date=row.close_date, is_listed=row.is_listed,
        )
        matched += 1
    result = {"matched": matched, "unmatched": unmatched, "skipped_errors": skipped_errors}
    log.info(
        "GMP poll: %d matched, %d unmatched, %d skipped (malformed)",
        matched, unmatched, skipped_errors,
    )
    return result


def main():
    from . import run as _run

    _run.setup_logging()
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = db.connect(config.DB_PATH)
    db.init_db(conn)
    try:
        poll_gmp_snapshots(conn)
        _run.send_gmp_update_alerts(conn)
    except Exception:
        log.exception("Unhandled error in gmp_source poll")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
