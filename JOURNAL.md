# Project Journal

Day-by-day, point-wise log of what actually happened on this project —
reconstructed from git history, file timestamps, and session records where
git history doesn't cover it (most of `research/` isn't committed yet).
Newest entries at the bottom. See `CLAUDE.md` for the architecture and
`FINDINGS.md` for the research write-up itself — this file is *when things
happened and why*, not the technical detail.

## 2026-09-06 — Phase 1: SEBI IPO collector built

- Initial commit; SEBI IPO collector implementation plan written
  (`docs/superpowers/plans/2026-09-06-sebi-ipo-collector.md`).
- Project scaffolded: SQLite persistence layer (`companies`/`filings`
  schema), SEBI public-issues listing row parser (hand-rolled regex, not
  BeautifulSoup — SEBI's HTML has invalid nested `<a>` tags).
- Switched package management from pip/`requirements.txt` to `uv`/
  `pyproject.toml`; `.python-version` tracked.
- Pagination added for SEBI's listing (page 1 plain GET, page ≥2 POST to
  a separate AJAX endpoint).
- Real DRHP/RHP PDF extraction added (a filing's actual PDF is only found
  via its detail page's `<iframe src>`, not the shorter "Abridged
  Prospectus" link).
- PDF downloader added: atomic writes (`.tmp` + `os.replace`), sha256
  hashing while streaming.
- Batched email alerting for new filings; discovery/download/alert wired
  into one orchestrated `run.py`.
- Setup docs, manual smoke test, and cron instructions written; cron
  schedule settled on Mon/Wed/Fri 9AM IST, install deferred to the real
  deployment target.
- Final review pass: fixed test discovery, rate limiting, logging, error
  visibility; all timestamps (DB + logs) switched from naive/UTC to IST.
- `CLAUDE.md` written up with build/test commands and architecture notes.
- **Also this day**: Phase 2 ideation doc drafted
  (`docs/superpowers/specs/2026-09-06-ipo-scoring-engine-design.md`) —
  brainstormed a scoring/recommendation engine on top of Phase 1, four
  candidate parameters (fundamentals, GMP, subscription, sentiment),
  weighed extraction-first vs. validate-first sequencing, recommended
  validating against historical data before building any live scorer.

## 2026-09-13 — Phase 2 research: dataset assembled, rev. 1 / rev. 2 validation

- `research/ipo-scoring-validation/` scaffolded (deliberately decoupled
  from the live collector): `fetch_data.py`, `build_dataset.py`,
  `README.md`.
- Historical dataset assembled: `ipo_dataset_3yr.csv` (294 IPOs, last 3
  years) from rohanbeingsocial/ipo-radar's 20-year Excel dataset;
  `crosscheck_report.csv` cross-checked a ~2-month overlap against a
  Kaggle GMP dataset (sanity-check only, not a feature source — see
  `README.md` for why).
- `analyze.py` rev. 1: replaced plain sklearn `LinearRegression`/
  in-sample-only R² with `statsmodels` OLS (p-values, CIs) + VIF + 5-fold
  CV, after external review flagged in-sample R² isn't predictive power.
  Caught `% QIB`/`% anchor` at r=1.000 (SEBI allocation templates, not a
  demand signal) causing a rank-deficient design matrix.
- `analyze.py` rev. 2: fixed a second collinearity issue — `Total Sub`/
  `QIB`/`NII`/`Retail` themselves collinear (VIF up to 111.7) — by
  splitting demand into two *alternative* specs (Model A: `Total Sub`
  only; Model B: `QIB`/`NII`/`Retail`), never combined. Switched to
  `GMP %` (normalized by offer price) instead of raw GMP — r≈0.81 vs.
  r≈0.42, and found to be mathematically identical to `Estimated Upside %`.
- The design doc's dataset-sourcing section updated with historical
  Indian IPO datasets found via web search (ipo-radar chosen as the base
  source).
- Separately: debugged `explore.ipynb`'s `ModuleNotFoundError: No module
  named 'statsmodels'` — root cause was a dead `%uv add statsmodels` cell
  running under the wrong Python (Windows Store Python, not the project's
  uv venv), which traced further to a VS Code/WSL kernel-selection issue
  (the `.venv` is Linux-only; VS Code's window wasn't connected to WSL).

## 2026-09-19 — rev. 3: methodology overhaul + regularization tuning

- User brought a detailed external review of rev. 2 (20 numbered points).
  Scoped via brainstorming/plan mode into 8 concrete steps, explicitly
  deferring feature-freezing and building the actual scoring formula
  (steps 9–10) per the user's own call.
- `analyze.py` rewritten: fixed `vif_table`'s missing constant column;
  added the full Model 1–5 hierarchy; added a matched-sample GMP
  comparison (same rows, no-GMP vs. +GMP %); **switched the primary CV
  metric from random 5-fold to time-based** (`TimeSeriesSplit` sorted by
  `List Date`, random-fold kept as a secondary benchmark); added MAE/RMSE
  and a `DummyRegressor` baseline everywhere; added a GMP-missingness
  check; added a Spearman-vs-Pearson nonlinearity check; first OLS vs.
  Ridge vs. ElasticNet comparison.
- **Result: time-based CV overturned rev. 2's headline number.** The
  "best" spec (fundamentals + Total Sub + GMP %) dropped from CV R²=0.327
  (random-fold) to 0.150 (time-based); fundamentals + GMP % alone (no
  Total Sub) turned out best under *both* metrics. GMP availability was
  also shown to be non-random — it skews toward stronger, earlier-listed
  issues, a real caveat on "GMP % is the strongest signal."
- `explore.ipynb` refactored to import `analyze.py`'s functions directly
  instead of a stale, duplicated sklearn implementation, so the notebook
  and script can't drift out of sync again (this exact drift was the
  review's #1 complaint). Added a LOWESS/residual diagnostic cell.
- `FINDINGS.md` rewritten as rev. 3 with real numbers from a verified run
  (`uv run analyze.py` + `nbconvert --execute` on the notebook, both
  exit 0 / zero errors).
- **Incident**: accidentally `rm -rf`'d the live `.venv` while it was in
  use by an active VS Code Jupyter kernel (Windows-shaped venv, files
  locked). Flagged immediately; rebuilt correctly via `uv sync` from WSL
  once the kernel was stopped, and re-verified everything against the
  rebuilt venv.
- Follow-up: tuned the regularization comparison — `RidgeCV`/
  `ElasticNetCV` (each picking alpha/l1_ratio via internal CV on the
  training fold only) replacing untuned `alpha=1.0` defaults. Ridge
  jumped from R²=0.253 to 0.464 once tuned; ElasticNet's l1_ratio choice
  turned out unstable across folds — flagged as a caveat, not glossed
  over. `FINDINGS.md` updated accordingly.
- `ipo_watcher`'s 23 existing tests verified passing throughout,
  unaffected by any `research/` changes.
- Started this journal.
