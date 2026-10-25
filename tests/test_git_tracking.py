import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_database_file_is_not_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "data/ipo_watcher.db"],
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0, "data/ipo_watcher.db must be trackable, not gitignored"


def test_pdf_directory_is_still_gitignored():
    result = subprocess.run(
        ["git", "check-ignore", "data/pdfs/example.pdf"],
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, "data/pdfs/ must remain gitignored"
