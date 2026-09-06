import re
import time
import datetime
from dataclasses import dataclass

import requests

BASE = "https://www.sebi.gov.in"
LISTING_URL = f"{BASE}/sebiweb/home/HomeAction.do"
PAGINATE_URL = f"{BASE}/sebiweb/ajax/home/getnewslistinfo.jsp"
# Public Issues, all filing sub-types combined (DRHP/RHP/Prospectus/Addenda/...)
SECTION_PARAMS = {"sid": "3", "ssid": "15", "smid": "-1"}

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 20
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3


@dataclass
class FilingRow:
    company_name: str
    filing_type: str
    filing_date: str  # ISO YYYY-MM-DD
    source_page_url: str


ROW_RE = re.compile(
    r"<tr[^>]*>\s*<td[^>]*>(?P<date>[^<]+)</td>\s*<td[^>]*>\s*"
    r"<a\s+href=\"(?P<url>https://www\.sebi\.gov\.in/filings/public-issues/[^\"]+\.html)\"[^>]*>"
    r"(?P<text>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def _parse_company_and_type(visible_text):
    text = visible_text.strip()
    if " - " in text:
        company_name, filing_type = text.rsplit(" - ", 1)
        return company_name.strip(), filing_type.strip()
    return text, "Unspecified"


def parse_listing(html):
    """Parse a listing page (or AJAX pagination fragment) into FilingRows.
    Handles rows where the title cell contains a nested, invalid <a> (e.g. a
    directly-linked Abridged Prospectus) by only taking the text before the
    first '<' — this discards the nested markup regardless of where the
    regex match itself terminated."""
    rows = []
    for m in ROW_RE.finditer(html):
        date_text = m.group("date").strip()
        try:
            filing_date = datetime.datetime.strptime(date_text, "%b %d, %Y").date().isoformat()
        except ValueError:
            continue
        visible_text = re.split(r"<", m.group("text"), maxsplit=1)[0]
        company_name, filing_type = _parse_company_and_type(visible_text)
        if not company_name:
            continue
        rows.append(FilingRow(
            company_name=company_name,
            filing_type=filing_type,
            filing_date=filing_date,
            source_page_url=m.group("url"),
        ))
    return rows


def _get_with_retries(url, params=None):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_exc


def _post_with_retries(url, data):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                data=data,
                headers={
                    "User-Agent": USER_AGENT,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
    raise last_exc


def fetch_listing_page(page_no=1):
    """Fetch raw HTML for one 1-indexed page of SEBI's Public Issues listing."""
    if page_no == 1:
        return _get_with_retries(LISTING_URL, params={"doListing": "yes", **SECTION_PARAMS})
    n = str(page_no - 1)
    data = {
        "nextValue": n,
        "next": "n",
        "search": "",
        "fromDate": "",
        "toDate": "",
        "fromYear": "",
        "toYear": "",
        "deptId": "",
        "sid": SECTION_PARAMS["sid"],
        "ssid": SECTION_PARAMS["ssid"],
        "smid": SECTION_PARAMS["smid"],
        "ssidhidden": SECTION_PARAMS["ssid"],
        "intmid": "-1",
        "sText": "Filings",
        "ssText": "Public Issues",
        "smText": "",
        "doDirect": n,
    }
    raw = _post_with_retries(PAGINATE_URL, data)
    return raw.split("#@#")[0]


DOC_IFRAME_RE = re.compile(
    r"<iframe[^>]+src=['\"]\.\./\.\./\.\./web/\?file=(?P<url>[^'\"]+)['\"]",
    re.IGNORECASE,
)


def fetch_detail_page(source_page_url):
    return _get_with_retries(source_page_url)


def parse_detail(html):
    """Extract the full DRHP/RHP PDF URL embedded via the detail page's
    iframe viewer. Deliberately ignores the separate Abridged Prospectus
    link that also appears on this page (see plan doc: out of scope).
    Returns None if no iframe is found."""
    m = DOC_IFRAME_RE.search(html)
    return m.group("url") if m else None
