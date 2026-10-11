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
- Ran the tuned regularization comparison against Model 3 too
  (it had only been checked against Model 4 so far). **This resolves the
  Model 3 vs. Model 4 question from earlier today**: Model 3 (fundamentals
  + GMP %, no Total Sub) wins on OLS, wins on RidgeCV, and wins on
  MAE/RMSE for both regularized estimators, using more data besides (131
  vs. 123 rows) — `Total Sub` doesn't look like it's earning its place
  once GMP % is in the model. `FINDINGS.md` recommendations updated
  accordingly; notebook and tests re-verified.
- Robustness sweep on Model 3 (the last item flagged in `FINDINGS.md` rec.
  #7): added `robustness_sweep()` checking the univariate GMP % → Listing
  Gain relationship across year/sector/Offer-Price-tercile subgroups (the
  full 8-predictor model needs far more rows than any subgroup has).
  **Holds up well**: every subgroup with n≥15 shows r=0.74–0.89, p<0.001 —
  not an aggregate artifact. Five sectors had too few GMP-complete rows to
  check (a real data gap); market regime still isn't checked at all (no
  market-index data in this dataset — flagged plainly, not glossed over).
  `FINDINGS.md`/notebook/tests updated and re-verified.

## 2026-09-20 — designing the actual Listing Gain scoring formula

- Moving on to feature selection + weightage now that the
  validation phase was done. Answer surfaced an important fact before
  proceeding: in Model 3, **only `GMP %` is individually significant**
  (p<0.05) — none of the 7 fundamentals are, and `Total Sub` had already
  been shown to add no validated value. Went through `superpowers:
  brainstorming` (classified architectural — a new subsystem, nothing
  scoring-related exists in `ipo_watcher` yet) rather than just picking
  weights informally, per `CLAUDE.md`'s explicit "no scoring logic without
  a real design pass" constraint.
- Scoped the design to *the formula + live GMP sourcing decision only* —
  DB schema, wiring into the live collector, and refresh/refit cadence
  stayed explicitly deferred (matching how `research/` was already
  decoupled from the live collector).
- **Mid-design pivot on Total Sub**: initially chose to keep it as a
  minor, unvalidated secondary factor. Testing the exact "GMP % + Total
  Sub, no fundamentals at all" spec for the first time (it had never
  actually been run — every prior spec either included fundamentals or
  gated the sample on their completeness) showed the opposite: GMP %
  alone on its true natural sample (n=147, not 131) hits **time-based CV
  R²=0.650** — the single strongest result of the whole project — while
  adding Total Sub *drops* it to 0.500. Total Sub dropped for good;
  formula is GMP %-only.
- Frozen formula: `predicted_listing_gain_% = -0.2206 + 1.0428 × GMP_%`
  (intercept ≈0, slope ≈1 — GMP % is close to an *unbiased* predictor of
  actual listing gain, not just correlated with it).
- Researched live GMP sourcing against `CLAUDE.md`'s "no cloud services"
  constraint: ruled out Apify's IPO Tracker and parse.bot's InvestorGain
  API (both paid cloud APIs) in favor of **IPO Guru's free API** (300
  req/day, covers GMP — found only after specifically asking for a free
  option), pending a real verification test call.
- **Caught a real mistake before it shipped**: the design initially
  presented uncertainty as "±12 percentage points" (the MAE). Asked
  directly whether that meant a hard bound (e.g. +18.4% predicted → lists
  between +6%/+30%) — it doesn't, and presenting MAE with a "±" symbol
  was actively misleading. Pulled the real out-of-sample residual
  distribution (120 historical CV predictions): heavily right-skewed
  (5th percentile −21.9, 95th +29.9, max overshoot +74.6 vs. max
  undershoot −37.6). Spec corrected to present an empirical, asymmetric
  5th/95th-percentile interval instead of a symmetric ± band.
- Wrote and staged
  `docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md`.
  Haven't reviewed/approved it yet myself; implementation (the
  `predict_listing_gain()` function, formalizing the GMP-%-only spec in
  `analyze.py`) hasn't started.

## 2026-10-11 — a recap, and the three implementation tasks

- Came back to the project after about three weeks away. Asked for a
  fresh recap in ASD-STE100 (Simplified Technical English) style, as an
  HTML page — published as an artifact first, then copied into the repo
  at `docs/recaps/2026-10-11-recap.html` as a real standalone document
  (had to add back the `<!doctype html>`/`<head>` wrapper the Artifact
  tool normally supplies automatically).
- Then did all three tasks the design spec had queued up:
  - **Model 0**: formalized the GMP-%-only spec in `analyze.py` (it had
    only existed as an ad-hoc check before). `uv run analyze.py`
    reproduces the exact numbers now (R²=0.650, MAE=12.26, n=147).
  - **`score.py`**: wrote `predict_listing_gain()` — the frozen formula,
    the empirical percentile interval, the exact output wording from the
    spec, with a runnable self-check.
  - **IPO Guru's API**: tested an unauthenticated call — got a real `401
    Unauthorized`, confirming the endpoint exists and isn't a
    documentation fiction. Getting an actual key still needs emailing
    `ipoguru.in[at]gmail.com` and waiting for a reply — a manual step,
    not something to automate.
- `FINDINGS.md` and the design spec both updated to record what is now
  actually done vs. still open. Tests still pass (23/23).

## 2026-10-11 (continued) — GMP storage, the live poller, and wiring the score into the email

- Designed and built the `gmp_snapshots` storage pass: one additive table
  plus `record_gmp_snapshot`/`get_latest_gmp_pct` in `db.py`, following the
  exact style already in use for `companies`/`filings`. No history query
  yet — nothing reads it back except "the latest value," per YAGNI.
- Then designed and built the actual live poller, `gmp_source.py`. Caught
  a real mistake before building anything on it: checked IPO Guru's real
  pricing page instead of trusting the earlier design doc's "300
  requests/day" claim, and found the free tier is actually **10/day,
  1/min, "Evaluation only."** Redesigned cadence around the real number —
  one unfiltered API call per poll (covers every tracked IPO at once),
  spread across market hours, a separate cron entry from the filing
  collector's. Corrected the earlier spec's wrong number once this was
  confirmed.
- Also had to pin the real response shape from IPO Guru's own API
  reference page (nested `gmp.percentage`/`gmp.price` as strings, a
  `{"data": [...]}` envelope, `name` not `display_name`) rather than
  guessing from their marketing copy.
- A fresh review of the finished poller (dispatched before committing)
  caught three real bugs I'd otherwise have shipped: the cron line as
  written would have fired 14 times/day, not 10 (cron's minute/hour
  fields multiply, they don't step together across an hour boundary) —
  guaranteed 429s on the afternoon polls, which matter most near a
  listing date; `parse_gmp_listing` crashed outright on an entry missing
  `name` or a `data: null` body, both realistic API responses; and
  `skipped_errors` was hard-coded to `0`, never actually counting
  anything. Fixed all three with failing tests first, each confirmed
  passing after.
- Finally wired the score into the live email: `run.py`'s
  `send_pending_alert` now reads the latest GMP % per filing's company at
  send time and attaches a predicted Listing Gain line (or "GMP not yet
  available" when there's no snapshot) before the email goes out. Applies
  to every row, corrigenda included, since GMP can move after the
  original filing.
- In the process, realized `score.py` (written under `research/` back
  when the formula was still just a research artifact) was never
  actually importable from there — that directory has no `__init__.py`
  and its name has a hyphen in it, so the "later live-wiring pass imports
  `predict_listing_gain`" the file's own docstring promised was never
  mechanically possible. Moved it into the live package as
  `ipo_watcher/scoring.py`, dropped its standalone self-check in favor of
  a real `tests/test_scoring.py`, matching every other module here.
- Asked for a full repo audit afterward, specifically looking for more
  of that same class of mistake (misplaced files, stale doc claims).
  Found and fixed: `CLAUDE.md`'s architecture/config/scope sections were
  still describing the pre-GMP collector as if nothing had changed; both
  implementation plans still showed every step unchecked despite being
  fully done; this journal itself had stopped three commits short of
  reality; and the `research/` directory's own README and FINDINGS.md
  still said things like "nothing here is wired into the live collector"
  after the formula they describe became exactly that.
- Tests: 54/54 passing throughout.
