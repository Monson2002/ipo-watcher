# IPO Scoring Validation — Findings (updated 2026-09-19, rev. 3)

Dataset: 294 IPOs listed in the last 3 years (`ipo_dataset_3yr.csv`). Analysis
via `analyze.py` — see `README.md` to reproduce.

**Revision history:**
- **rev. 1** replaced plain sklearn `LinearRegression`/in-sample-only R²
  with `statsmodels` OLS (p-values, CIs), VIF, and 5-fold cross-validated
  R², after external review correctly flagged in-sample R² isn't
  predictive power. That pass also caught `% QIB`/`% anchor` at r=1.000
  (SEBI-mandated allocation templates, not a demand signal) causing a
  rank-deficient design matrix.
- **rev. 2** fixed a second collinearity issue: `Total Sub`, `QIB`, `NII`,
  `Retail` are themselves severely collinear (VIF up to 111.7 combined), so
  demand is now fit as two **alternative** specs — Model A (`Total Sub`
  only) and Model B (`QIB`/`NII`/`Retail`) — never combined. Also switched
  to **`GMP %`** (GMP normalized by offer price) instead of raw GMP —
  substantially stronger (r≈0.81 vs r≈0.42) and scale-invariant.
- **rev. 3** (this version), after a second external review: fixed VIF to
  use an explicit constant column, added two missing model specs
  (fundamentals-only, fundamentals+GMP%-only) to reach a full 1–5 model
  hierarchy, and — the big one — **replaced random 5-fold CV with
  time-based (walk-forward) CV as the primary out-of-sample metric**,
  kept random-fold as a secondary benchmark, added MAE/RMSE and an
  explicit `DummyRegressor(mean)` baseline everywhere, ran a matched-sample
  GMP comparison (same rows, no-GMP vs +GMP%), tested whether GMP
  availability is random, and did a first OLS-vs-Ridge-vs-ElasticNet pass.
  **This materially changes the headline conclusion — read below before
  reusing rev. 2's numbers.**
- **2026-10-11 addendum**: the GMP-%-only spec (dropping fundamentals
  entirely, not just leaving them non-significant) was formalized as
  **Model 0** in `analyze.py` — previously it only existed as an ad-hoc
  check from the scoring-formula design session. `uv run analyze.py` now
  reproduces it directly (R²=0.650, MAE=12.26, RMSE=16.55 for Listing
  Gain, n=147). The frozen formula itself was written here as `score.py`
  and later moved into the live package as `ipo_watcher/scoring.py`'s
  `predict_listing_gain()` once `run.py` actually wired it into the email
  alert — see
  `docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md`.

## Headline result, revised: time-based CV shows much weaker, noisier performance than rev. 2 reported

Rev. 2's random 5-fold CV R²=0.327 for the leading spec (fundamentals +
Total Sub + GMP %, Listing Gain) held up as a **within-sample-order**
estimate, but IPOs are time-ordered and a scoring model has to predict
*future* IPOs from *past* ones — random shuffling lets folds "peek" at
issues that listed after the ones they're predicting. Sorting by `List
Date` and using `TimeSeriesSplit` instead tells a different story:

| Model (→ Listing Gain) | n | Time-based CV R² (primary) | Random 5-fold CV R² (secondary, rev. 2's metric) |
|---|---|---|---|
| 1: fundamentals only | 202 | -1.528 | -0.166 |
| 2: fundamentals + Total Sub | 194 | -0.167 | 0.155 |
| **3: fundamentals + GMP %** | 131 | **0.190 (best time-based result)** | 0.581 |
| 4: fundamentals + Total Sub + GMP % | 123 | 0.150 | 0.327 |
| 5: fundamentals + QIB/NII/Retail | 194 | -0.200 | 0.113 |
| 5b: fundamentals + QIB/NII/Retail + GMP % | 123 | -0.013 | 0.179 |

Every model's time-based R² is lower than its random-fold R², several are
negative where random-fold was clearly positive (Models 1, 5, 5b), and the
fold-to-fold **range is wide** (e.g. Model 4: [-0.630, 0.622]) — at this
sample size, walk-forward CV has only a handful of test folds, so treat
these as noisy point estimates, not settled numbers. MAE/RMSE tell the
same story in absolute terms: Model 4's time-based MAE is 17.69 percentage
points (RMSE 22.94) vs. the mean-prediction baseline's 23.68 MAE
(RMSE 31.55) — a real but modest improvement over "just guess the average,"
not the strong result rev. 2's R²=0.327 implied.

**Model 3 (fundamentals + GMP %, no Total Sub) is the best-performing spec
under both metrics**, not Model 4 — dropping `Total Sub` gives a *better*
time-based R² (0.190 vs 0.150) *and* a better random-fold R² (0.581 vs
0.327). Adding `Total Sub` on top of GMP % does not clearly help
out-of-sample, despite `Total Sub` being individually significant
(p=0.002) in-sample in Model 4 — a sign it's adding in-sample fit without
adding real predictive power once GMP % is already in the model.

## Matched-sample comparison: GMP % still helps, but on a much more modest scale

Restricting Model 2 (no GMP) and Model 4 (+GMP %) to the *same* 123 rows
(so the comparison isn't confounded by the sample-size drop that comes
with requiring GMP):

| | No GMP % | + GMP % |
|---|---|---|
| Time-based CV R² (n=123, same rows) | -0.237 | 0.150 |
| Random 5-fold CV R² (same rows) | -0.486 | 0.327 |

GMP % moves both metrics from negative to positive — the improvement is
real, not just a sample-composition artifact. But the *matched-sample*
no-GMP number (-0.237/-0.486) is worse than the *full-sample* no-GMP
number reported in the model-hierarchy table above (-0.167/0.155,
n=194) — the extra ~70 rows available without requiring GMP are
themselves informative, another reason the matched comparison and the
"use whatever data's available" comparison can legitimately disagree.

## Confirms rev. 2, now worse than reported: 6/12/24-month returns don't generalize

Time-based CV R² for every model is negative at every longer horizon,
often severely so — Ret 6m %'s best model still scores -3.76, Ret 12m %'s
best is -1.72, and even Ret 24m %'s best (Model 5, -0.260) never turns
positive. **The tested models do not generalize well at the longer
horizons, with cross-validated R² well below zero in every specification**
— stronger and more consistent than rev. 2's "some positive, some negative"
picture, because rev. 2's Ret 24m %=0.198 result (Model A, no GMP) came
from random-fold CV; time-based CV puts that same spec at -0.777. Still
don't build a medium/long-term score from this dataset.

## GMP availability is not random — a real caveat on "GMP % is the strongest signal"

`GMP %` is available on 147/294 rows. Testing whether that split is
associated with anything else in the data:

| | Available (n=147) | Missing (n=147) | Mann-Whitney p |
|---|---|---|---|
| Listing Gain (median) | 17.27 | 6.66 | **0.0003** |
| Total Sub (median) | 41.54 | 28.40 | **0.0058** |
| Offer Price (median) | 283.00 | 227.00 | 0.215 |
| List year (median) | 2024 | 2025 | **0.0133** |

Sector shows no association (chi² p=0.680), but IPOs with a reported GMP %
had **significantly higher listing-day gains, higher subscription, and
skewed toward earlier years** in this dataset than IPOs without one. This
means the correct statement is: **among IPOs for which GMP % happened to
be available — which were themselves somewhat stronger issues — GMP % was
the strongest tested individual signal.** Whether that holds for the
IPOs currently missing GMP data (including, plausibly, weaker or more
recent issues) is untested and shouldn't be assumed.

## Nonlinearity check: GMP % looks linear, Total Sub/QIB less so

Spearman rank correlation alongside Pearson r for the top parameters
(Listing Gain):

| Parameter | Pearson r | Spearman r |
|---|---|---|
| GMP % | 0.810 | 0.821 |
| Total Sub | 0.622 | 0.684 |
| QIB | 0.602 | 0.700 |

GMP %'s Spearman and Pearson are close — a linear OLS term is a reasonable
functional form for it. `Total Sub` and `QIB` show a larger gap (Spearman
noticeably higher), suggesting their true relationship to Listing Gain is
monotonic but not well captured by a single linear coefficient — worth a
log-transform or spline term if either becomes a serious scoring input
later, not acted on in this pass.

## Post-IPO P/E at 24 months — unchanged from rev. 2

Still an exploratory hypothesis, not a finding: significant across
specifications at the 24-month horizon (n=51), but small-sample and not
corroborated by an out-of-sample result (every 24-month model's time-based
R² is negative). Revisit with more data before acting on it.

## Regularization, tuned: Ridge/ElasticNet still beat OLS — and now checked against Model 3 too

Same time-based folds, `RidgeCV`/`ElasticNetCV` each picking their own
alpha (and l1_ratio, for ElasticNet) via their own internal CV, fit only
on the training portion of each outer fold — no leakage from the outer
test fold. Now run against **both** leading specs, not just Model 4:

| Estimator | Model 3 (n=131) | Model 4, matched (n=123) |
|---|---|---|
| OLS | R²=0.190, MAE=15.85, RMSE=21.24 | R²=0.150, MAE=17.69, RMSE=22.94 |
| RidgeCV (tuned) | **R²=0.499**, MAE=12.82, RMSE=17.05 | R²=0.464, MAE=14.50, RMSE=18.97 |
| ElasticNetCV (tuned) | R²=0.506, **MAE=12.45, RMSE=16.85** | **R²=0.522**, MAE=13.95, RMSE=18.43 |

**This tips the Model 3 vs. Model 4 question toward Model 3**: it wins on
OLS, wins on RidgeCV, and wins on MAE/RMSE for *both* regularized
estimators — the one number where Model 4 comes out ahead is
ElasticNetCV's R² specifically (0.522 vs 0.506), a thin margin against
three other metrics pointing the other way, on top of Model 3 already
using more data (131 vs 123 rows) since it doesn't need `Total Sub`.

The untuned first pass (fixed `alpha=1.0`) had understated Ridge
specifically — tuned Ridge picks alpha=5.6–10 (Model 3) or 10–17.8
(Model 4), well above the arbitrary default of 1.0. Both regularized
variants still clearly beat plain OLS on both specs, confirming OLS is
overfitting a bit at this sample size rather than the earlier untuned
numbers being a fluke of a lucky default.

One caveat worth flagging on both specs: ElasticNetCV's *l1_ratio* choice
is unstable across folds (ranges from 0.1 to 1.0 — plain Ridge-like to
pure Lasso — depending on the fold), which usually signals the training
folds are too small/different from each other for the inner tuning CV to
settle on a consistent regularization type. Treat the R²≈0.5 numbers as
"regularization clearly helps here," not as "this exact configuration is
the answer."

## Robustness sweep: the GMP % relationship holds up well across subgroups

Checked whether Model 3's core `GMP %` → Listing Gain relationship is an
aggregate artifact or genuinely holds across time, sector, and IPO size.
The full 8-predictor Model 3 needs far more rows than any subgroup has
(~10 rows/predictor → ~80+ needed), so this checks the **univariate**
relationship (Pearson r + a 2-parameter OLS: intercept + GMP %) per
subgroup instead — the core signal the full model rests on. Subgroups
under n=15 are reported as "too few rows," not given a misleadingly
precise number:

| Slice | n | Pearson r | GMP % slope (p) |
|---|---|---|---|
| **By year** — 2024 | 85 | 0.820 | 1.035 (p<0.001) |
| 2025 | 33 | 0.866 | 1.117 (p<0.001) |
| 2026 | 13 | — | too few rows |
| **By sector** — Industrials | 22 | 0.875 | 1.151 (p<0.001) |
| Energy | 19 | 0.861 | 0.857 (p<0.001) |
| Other | 24 | 0.854 | 0.999 (p<0.001) |
| Consumer Discretionary | 18 | 0.742 | 0.559 (p<0.001) |
| Financials/Healthcare/Materials/Real Estate/Technology | 3–14 each | — | too few rows |
| **By Offer Price tercile** — small | 44 | 0.788 | 1.071 (p<0.001) |
| mid | 43 | 0.892 | 1.187 (p<0.001) |
| large | 44 | 0.841 | 0.845 (p<0.001) |

**Every subgroup with enough data (n≥15) shows a strong, highly
significant, positive GMP % relationship** — Pearson r ranges only
0.74–0.89 across 10 different slices, and the OLS slope is positive and
significant (p<0.001) in every one. This is real evidence the relationship
isn't a fluke of the aggregate sample or concentrated in one sector/year/
size band — it's consistent. Five sectors (Financials, Healthcare,
Materials, Real Estate, Technology) had too few GMP-%-complete rows
(3–14 each) to check at all; that's a genuine data gap, not a negative
result.

**Market regime was not checked** — this dataset has no market-index data
(e.g. NIFTY) to derive bull/neutral/weak periods from. Faking a proxy from
`list_year` would just duplicate the time-period slice above while
implying regime was accounted for when it wasn't. A real check needs an
external market-index series merged in by `List Date`.

## Recommendations, updated for rev. 3

1. **Model 3 (fundamentals + GMP %, no Total Sub) is the more consistently
   supported spec, not Model 4.** It wins on every metric checked except
   ElasticNetCV's R² specifically (by a thin margin) — OLS, RidgeCV, and
   MAE/RMSE for both regularized estimators all favor Model 3, and it uses
   more data (131 vs. 123 rows) since it doesn't need `Total Sub`. Not
   airtight given how wide the CV fold ranges are at this sample size, but
   `Total Sub` no longer looks like it's earning its place once GMP % is
   in the model.
2. **GMP % (alone, per #1) is a leading candidate, not a validated
   production formula.** Its absolute out-of-sample performance is much
   more modest than rev. 2 reported for the Total-Sub-included spec
   (R²≈0.15–0.19 with OLS, not 0.327), and the true generalization
   estimate is noisy with this few time-ordered folds — regularization
   (#5) closes most of that gap, but isn't validated as a final model yet
   either.
3. **GMP availability is not random** — reported GMP % skews toward
   stronger, earlier-listed issues. Any claim about GMP %'s predictive
   power implicitly applies to that subpopulation, not to IPOs in general.
4. **Still don't build a 6/12/24-month score from this dataset** — every
   model at every longer horizon has negative time-based CV R², worse than
   rev. 2's random-fold estimate suggested.
5. **Tuned regularization (RidgeCV/ElasticNetCV) clearly beats OLS
   out-of-sample on both Model 3 and Model 4** (R²≈0.46–0.52 vs OLS's
   0.15–0.19) — but ElasticNetCV's l1_ratio choice is unstable across
   folds on both specs, so treat this as "regularization helps here," not
   as a validated production model.
6. **Always report time-based CV (or a real held-out future-IPO test),
   never random-fold CV alone** — the two disagree enough on this dataset
   (e.g. Model 3: 0.190 vs 0.581) that random-fold alone would have been
   actively misleading about how this would perform on genuinely future
   IPOs.
7. **Robustness sweep: checked, holds up.** The GMP % → Listing Gain
   relationship is consistently strong (r=0.74–0.89, p<0.001) across
   every year/sector/size subgroup with enough data — not an aggregate
   artifact. Five sectors had too few rows to check (a data gap, flagged
   above) and market regime still isn't checked at all (no market-index
   data in this dataset — the one item from the original review's list
   that genuinely needs a new data source, not just more analysis).
