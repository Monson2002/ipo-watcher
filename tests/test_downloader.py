import hashlib
import os
from unittest.mock import patch, MagicMock
import tempfile

from ipo_watcher import downloader


def test_slugify():
    assert downloader.slugify("Gaurik Fashions Limited") == "gaurik_fashions_limited"
    assert downloader.slugify("Addendum to DRHP") == "addendum_to_drhp"


def test_build_pdf_path():
    path = downloader.build_pdf_path(
        "data/pdfs", "Gaurik Fashions Limited", "2026-09-04", "Addendum to DRHP", "abcd1234ef567890"
    )
    assert path == os.path.join(
        "data/pdfs", "gaurik_fashions_limited", "2026-09-04_addendum_to_drhp_abcd1234.pdf"
    )


def test_download_pdf_writes_file_and_returns_sha256(tmp_path):
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.iter_content = MagicMock(return_value=[b"PDF-BYTES-HERE"])
    fake_resp.__enter__ = MagicMock(return_value=fake_resp)
    fake_resp.__exit__ = MagicMock(return_value=False)

    dest = str(tmp_path / "co" / "file.pdf")
    with patch("ipo_watcher.downloader.requests.get", return_value=fake_resp) as mock_get:
        digest = downloader.download_pdf("https://example.com/a.pdf", dest)

    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == b"PDF-BYTES-HERE"
    expected_digest = hashlib.sha256(b"PDF-BYTES-HERE").hexdigest()
    assert digest == expected_digest
    assert not os.path.exists(dest + ".tmp")
    mock_get.assert_called_once()
