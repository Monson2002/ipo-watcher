import hashlib
import os
import re
import time

import requests

USER_AGENT = "Mozilla/5.0 (compatible; ipo-watcher/1.0)"
TIMEOUT = 60
MAX_RETRIES = 3
RETRY_SLEEP_SECONDS = 3


def slugify(text):
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def build_pdf_path(pdf_dir, company_name, filing_date, filing_type, dedup_key):
    company_slug = slugify(company_name)
    type_slug = slugify(filing_type)
    filename = f"{filing_date}_{type_slug}_{dedup_key[:8]}.pdf"
    return os.path.join(pdf_dir, company_slug, filename)


def download_pdf(document_url, dest_path):
    """Download document_url to dest_path via a temp file + atomic rename.
    Returns the sha256 hex digest of the downloaded bytes."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp_path = dest_path + ".tmp"
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            digest = hashlib.sha256()
            with requests.get(
                document_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True
            ) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            digest.update(chunk)
            os.replace(tmp_path, dest_path)
            return digest.hexdigest()
        except requests.RequestException as exc:
            last_exc = exc
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_SLEEP_SECONDS)
        except OSError:
            # Not a network hiccup (e.g. disk full) - don't retry, but don't
            # leave a partial .tmp file behind either.
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    raise last_exc
