# IPO scoring validation (research, not part of the live collector)

Historical-dataset-first validation of the scoring params from
`docs/superpowers/specs/2026-09-06-ipo-scoring-engine-design.md`. Findings
are in `FINDINGS.md`. This directory is deliberately decoupled from the
`ipo_watcher` package — nothing here is wired into the live collector.

## Reproduce

```bash
export KAGGLE_USERNAME=...   # optional - only needed for the cross-check dataset
export KAGGLE_KEY=...
uv run fetch_data.py         # downloads data/ (gitignored, re-fetchable)
uv run build_dataset.py      # -> ipo_dataset_3yr.csv, crosscheck_report.csv
uv run analyze.py            # correlations + regression, prints to stdout
```

Or interactively: `explore.ipynb` (same analysis, cell-by-cell). `matplotlib`,
`scikit-learn`, `statsmodels`, and `jupyterlab` are real dependencies in the
main project's `pyproject.toml` (not just inline PEP 723 script deps) so a
Jupyter kernel pointed at the project's own `.venv` has everything it needs
without extra flags — open it with `uv run jupyter lab explore.ipynb`, or
point VS Code's Jupyter extension at `.venv`. `fetch_data.py`/
`build_dataset.py`/`analyze.py` still declare their deps inline (PEP 723)
too, so they work standalone even without `uv sync`.

## What's committed vs. not

- `data/` — gitignored. Raw pulls from ipo-radar (GitHub) and Kaggle,
  re-fetchable by `fetch_data.py`, not worth committing.
- `ipo_dataset_3yr.csv`, `crosscheck_report.csv` — committed. These are the
  actual deliverables: the assembled 3-year dataset and the cross-check
  comparison.
- `FINDINGS.md` — committed. Human-readable summary of what the analysis
  showed.

## Sources

- **Base dataset**: [rohanbeingsocial/ipo-radar](https://github.com/rohanbeingsocial/ipo-radar)'s
  `finalipodata_expanded_20yr.xlsx`, "Expanded" sheet — 976 NSE/BSE
  mainboard IPOs, Feb 2005 onward, auto-updated daily from Chittorgarh +
  Yahoo Finance + RHP analysis.
- **Cross-check only, not a feature source**: Kaggle's
  [ipo-watch-debuts-gmps-trajectories](https://www.kaggle.com/datasets/nikhilraj7700/ipo-watch-debuts-gmps-trajectories)
  — turned out to be a single-snapshot dataset (not an actual GMP time
  series despite the title) covering Sep 2020–Nov 2023, which barely
  overlaps the 3-year window. Used only to sanity-check ipo-radar's numbers
  against another source for their ~2-month overlap — see `FINDINGS.md` for
  what that turned up.
