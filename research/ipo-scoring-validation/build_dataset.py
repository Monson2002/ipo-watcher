# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "openpyxl"]
# ///
"""
Builds ipo_dataset_3yr.csv from ipo-radar's dataset, filtered to IPOs that
listed in the last 3 years and actually have a known outcome.

Also cross-checks (does NOT merge) against the Kaggle GMP-performance CSV
for whatever IPOs overlap in time - see README.md for why that dataset is
a cross-check only, not a feature source (it barely overlaps the 3-year
window and has no fundamentals of its own).

Run: uv run build_dataset.py
"""
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
YEARS_BACK = 3

FUNDAMENTALS_COLS = ["ROE", "ROCE", "D/E", "PAT Margin", "P/B", "Post IPO P/E", "EBITA Margin"]
SUBSCRIPTION_COLS = ["QIB", "bNII", "sNII", "NII", "Retail", "Total Sub", "% QIB", "% Retail", "% anchor"]
OUTCOME_COLS = ["Day1 Close", "Listing Gain", "Ret 6m %", "Ret 12m %", "Ret 24m %"]
IDENTITY_COLS = ["cg_ipo_id", "Name", "List Date", "Sector"]
GMP_COLS = ["GMP", "Offer Price", "Estimated Price"]

KEEP_COLS = IDENTITY_COLS + FUNDAMENTALS_COLS + GMP_COLS + SUBSCRIPTION_COLS + OUTCOME_COLS


def _normalize_name(name):
    """Strip common suffixes/noise so names from two different sources can
    be compared: 'ASK Automotive IPO (ASKAUTOLTD,544022)' and
    'ASK Automotive Ltd.' should both normalize to 'ask automotive'."""
    name = re.sub(r"\(.*?\)", "", name)  # drop parenthetical (SYMBOL,code)
    name = re.sub(r"\bIPO\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b(Ltd\.?|Limited|Pvt\.?)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return name.strip()


def load_ipo_radar():
    df = pd.read_excel(DATA_DIR / "ipo_radar_20yr.xlsx", sheet_name="Expanded")
    df["List Date"] = pd.to_datetime(df["List Date"], format="%d-%b-%Y", errors="coerce")
    return df


def filter_last_n_years(df, years):
    cutoff = datetime.now() - timedelta(days=365 * years)
    listed = df[df["Listing Gain"].notna()]  # only IPOs with a known outcome
    recent = listed[listed["List Date"] >= cutoff]
    print(f"ipo-radar: {len(df)} total rows -> {len(listed)} with known outcome -> {len(recent)} in last {years}yr")
    return recent


def cross_check_against_kaggle(radar_df):
    gmp_files = list(DATA_DIR.glob("*GMP*.csv"))
    if not gmp_files:
        print("no Kaggle GMP CSV found - skipping cross-check (see README.md)")
        return
    kaggle_df = pd.read_csv(gmp_files[0])
    kaggle_df["Listing Date"] = pd.to_datetime(kaggle_df["Listing Date"], format="%d-%m-%Y")
    kaggle_df["_norm_name"] = kaggle_df["IPO"].apply(_normalize_name)

    lo, hi = kaggle_df["Listing Date"].min(), kaggle_df["Listing Date"].max()
    window = radar_df[(radar_df["List Date"] >= lo) & (radar_df["List Date"] <= hi)].copy()
    window["_norm_name"] = window["Name"].apply(_normalize_name)

    merged = window.merge(kaggle_df, on="_norm_name", how="inner", suffixes=("_radar", "_kaggle"))
    print(f"\ncross-check window {lo.date()}..{hi.date()}: "
          f"{len(window)} ipo-radar rows, {len(kaggle_df)} kaggle rows, {len(merged)} matched by name")

    if merged.empty:
        print("no name matches in the overlap window - nothing to compare")
        return

    report = merged[["Name", "GMP_radar", "GMP_kaggle", "Listing Gain", "Listing Percentage"]].rename(
        columns={"Listing Gain": "radar_ListingGain_pct", "Listing Percentage": "kaggle_ListingGain_pct"}
    )
    report_path = Path(__file__).parent / "crosscheck_report.csv"
    report.to_csv(report_path, index=False)
    print(f"wrote {report_path} ({len(merged)} matched rows) - inspect for discrepancies")


def main():
    radar = load_ipo_radar()
    recent = filter_last_n_years(radar, YEARS_BACK)
    cross_check_against_kaggle(radar)

    out = recent[KEEP_COLS].sort_values("List Date")
    out_path = Path(__file__).parent / "ipo_dataset_3yr.csv"
    out.to_csv(out_path, index=False)
    print(f"\nwrote {out_path}: {len(out)} rows, {len(KEEP_COLS)} columns")
    print("missing-value counts per column:")
    print(out.isna().sum().to_string())


if __name__ == "__main__":
    main()
