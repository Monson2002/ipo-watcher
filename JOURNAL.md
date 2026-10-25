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
- Brainstormed the remaining "refresh/refit cadence" open question
  (left over from the scoring-formula and poller design docs). Split it
  in two: refit cadence (retraining `scoring.py`'s coefficients) stays a
  documented manual-review policy — parked until the real API key
  arrives and some real usage happens. Refresh cadence turned into its
  own architectural design: a follow-up "GMP moved" email whenever a
  tracked company's GMP shifts ≥5 percentage points from the value last
  actually emailed, with extra emphasis when `close_date` (bidding
  closes today) is today, and a permanent stop once `is_listed` or
  `close_date` says the window is over.
- Built it: a new `gmp_notifications` table (what GMP value was last
  actually emailed, separate from `gmp_snapshots`' every-poll history),
  `close_date`/`is_listed` columns added directly to the already-shipped
  `gmp_snapshots` table (safe only because nothing's deployed yet — no
  migrations framework exists), and `run.py`'s new
  `send_gmp_update_alerts`, called from `gmp_source.py`'s own cron cycle
  right after polling — zero new cron entries, zero new API calls.
- A fresh review caught one real bug before committing: `send_pending_alert`
  was recording the GMP baseline even when the filing email failed to
  send, unlike that function's own established retry contract (filings
  stay unnotified and the whole batch retries). Fixed. A second,
  symmetric case for the update-alert path was flagged too, but that one
  was an intentional, already-reasoned spec decision — left as specified.
- Caught my own float bug right after: `abs(latest - last_notified) >=
  5.0` can miss a true exact 5.00pp move because of floating-point
  imprecision (4.20→9.20 computes as 4.999999999999999). Fixed by
  rounding the comparison.
- Asked for one more pass over every deferred minor from both GMP
  reviews (the poller's and the update-alerts') and cleared the whole
  list in one batch: a NaN/infinite `gmp_pct` guard, defensive parsing
  for `is_listed`/`close_date` (string "true"/"false", malformed dates —
  all degrade to `None` instead of crashing the whole poll),
  deterministic tie-breaking in `match_company` on a name-normalization
  collision, narrowed the HTTP retry net to connection/timeout errors
  only, fail-fast on a missing `IPO_GURU_API_KEY`, a mislabeled exception
  message, a log-wording grammar nit, and the handful of test gaps the
  reviewers named. The one item left as documentation-only rather than
  code: the "permanent" stop re-checks live state every poll rather than
  latching a flag — correcting the spec's wording to match reality
  instead of adding schema for a flapping-data scenario nothing has
  actually shown happening.
- Caught `CLAUDE.md` and this journal drifting stale again, same class
  of mistake as the first audit — neither mentioned `gmp_notifications`,
  `send_gmp_update_alerts`, or any of this work. Fixed both.
- Tests: 91/91 passing throughout this whole arc.

## 2026-10-25 — a real API key, a near-miss, and the v1-to-v2 fix it surfaced

- Wrote a fresh status report (`docs/recaps/2026-10-11-status-report.html`)
  in ASD-STE100 style, published as an artifact, for review this week —
  what works, what the review process caught, and the handful of
  remaining open items.
- Got a real IPO Guru API key. Confirmed it immediately with a real
  `/me` call before touching any code — plan `free`, 10/day, 1/minute,
  `gmp_history_days: 0`. Stored it in `.env` only, never in a committed
  file.
- Caught a real near-miss along the way: `.env.example` (tracked by git,
  meant to hold only placeholders) briefly had a real Gmail app password
  and other live credentials sitting in it on disk, mid-edit. Confirmed
  via `git diff HEAD` that nothing had been committed or pushed yet, and
  flagged it immediately rather than touching the file myself while it
  was actively being edited. It got cleared before anything landed in
  git history.
- A real `/ipos` call surfaced something no amount of documentation
  reading would have: IPO Guru's `/api/v1` is retired. The live poller
  has been pointing at a dead endpoint this whole time and has never
  actually run against real data.
- Fetched the real v2 reference and checked it field by field against
  what `gmp_source.py` already assumed. Almost everything matched
  exactly — the defensive parsing written before any real key existed
  turned out correct on the first real response. Two real findings: the
  `/ipos` endpoint defaults to a 3-month window (an undocumented limit,
  removed with `months=0`), and `/gmp` — the obviously-named
  alternative — turns out to omit `close_date`/`is_listed` entirely,
  which would have silently broken the update-alerts stopping condition
  had I switched to it for being "simpler."
- Wrote the spec and plan, then the whole fix was one line:
  `IPOS_URL` from the dead v1 path to the real v2 one, plus a regression
  test pinning the exact URL string. Self-reviewed it rather than
  dispatching a fresh reviewer — genuinely too small a change to be
  worth a second context.
- Tests: 92/92.
- Ran the poller for real for the first time ever, against real data.
  Hit the 1/minute burst limit twice before timing it correctly — a
  `/me` check and a poll in the same minute is already too many calls.
  Once it ran clean: 7 matched, 257 unmatched, 83 skipped. The large
  unmatched/skipped counts are expected, not bugs — IPO Guru tracks
  hundreds of historical IPOs against the 9 companies our own collector
  has actually discovered, and many of those are old enough that their
  GMP data has gone stale. The one real finding: all 7 matched companies
  already show `is_listed: true` — every filing discovered so far has
  already completed listing, so the update-alert stopping condition
  applies to all of them immediately. The pipeline works; it just has no
  live target yet. That starts mattering the next time the SEBI
  collector discovers a filing still in its bidding window.
- Looked into actually deploying this. Confirmed nothing is installed
  anywhere real — not on any server, not even a cron daemon running on
  my own WSL machine. Oracle's free tier wanted a card for identity
  verification, which I do not have. No spare always-on machine at home
  either right now. Deployment stays blocked on infrastructure access,
  not on anything code-related — parked until I have a card or a machine
  that can stay on.
