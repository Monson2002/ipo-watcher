# /// script
# requires-python = ">=3.11"
# dependencies = ["requests"]
# ///
"""
Downloads the two source datasets for the IPO scoring validation research.

Run: uv run fetch_data.py
(the Kaggle download step also needs KAGGLE_USERNAME/KAGGLE_KEY in the
environment - see README.md in this directory)
"""
import os
import subprocess
import sys
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"

IPO_RADAR_URL = (
    "https://raw.githubusercontent.com/rohanbeingsocial/ipo-radar/main/"
    "ipodata/finalipodata_expanded_20yr.xlsx"
)
IPO_RADAR_DEST = DATA_DIR / "ipo_radar_20yr.xlsx"

KAGGLE_DATASET = "nikhilraj7700/ipo-watch-debuts-gmps-trajectories"


def fetch_ipo_radar():
    if IPO_RADAR_DEST.exists():
        print(f"skip: {IPO_RADAR_DEST} already exists")
        return
    resp = requests.get(IPO_RADAR_URL, timeout=30)
    resp.raise_for_status()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IPO_RADAR_DEST.write_bytes(resp.content)
    print(f"wrote {IPO_RADAR_DEST} ({len(resp.content)} bytes)")


def fetch_kaggle_gmp():
    existing = list(DATA_DIR.glob("*GMP*.csv"))
    if existing:
        print(f"skip: {existing[0]} already exists")
        return
    if not (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")):
        print(
            "skip: KAGGLE_USERNAME/KAGGLE_KEY not set in environment "
            "(this dataset is optional - it's only used as a cross-check, "
            "not a feature source, see README.md)",
            file=sys.stderr,
        )
        return
    subprocess.run(
        [
            "uvx",
            "kaggle",
            "datasets",
            "download",
            "-d",
            KAGGLE_DATASET,
            "-p",
            str(DATA_DIR),
            "--unzip",
        ],
        check=True,
    )


if __name__ == "__main__":
    fetch_ipo_radar()
    fetch_kaggle_gmp()
