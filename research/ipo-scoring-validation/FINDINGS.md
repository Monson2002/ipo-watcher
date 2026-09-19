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

## Regularization, tuned: Ridge/ElasticNet still beat OLS out-of-sample

On Model 4 (matched sample, n=123), same time-based folds.
`RidgeCV`/`ElasticNetCV` each pick their own alpha (and l1_ratio, for
ElasticNet) via their own internal CV, fit only on the training portion of
each outer time-based fold — no leakage from the outer test fold:

| Estimator | Time-based CV R² | MAE | RMSE |
|---|---|---|---|
| OLS | 0.150 | 17.69 | 22.94 |
| RidgeCV (tuned) | 0.464 | 14.50 | 18.97 |
| ElasticNetCV (tuned) | **0.522** | **13.95** | **18.43** |

The untuned first pass (fixed `alpha=1.0`) understated Ridge specifically —
tuned Ridge nearly matches tuned ElasticNet (0.464 vs 0.522) once it's
allowed to pick a real alpha (10–17.8 across folds, well above the
arbitrary default of 1.0). Both regularized variants still clearly beat
plain OLS, confirming OLS is overfitting a bit at this sample size (8
predictors, ~123 rows) rather than the earlier untuned numbers being a
fluke of a lucky default.

One caveat worth flagging: ElasticNetCV's *l1_ratio* choice is unstable
across folds (0.1 on 3 folds, 1.0 — i.e. pure Lasso — on 2), which usually
signals the training folds are too small/different from each other for
the inner tuning CV to settle on a consistent regularization type. Treat
the 0.522 R² as "regularization clearly helps here," not as "this exact
ElasticNet configuration is the answer."

## Recommendations, updated for rev. 3

1. **Don't freeze Model 4 (fundamentals + Total Sub + GMP %) as "the"
   Listing Gain spec.** Model 3 (fundamentals + GMP %, no Total Sub) is the
   better-performing spec under the primary time-based metric; the two are
   close enough (0.190 vs 0.150) that this should be treated as an open
   question, not settled, especially given how wide the CV fold ranges are
   at this sample size.
2. **GMP % + Total Sub is a leading candidate, not a validated production
   formula.** It clears matched-sample validation (helps on the same rows)
   but its absolute out-of-sample performance is much more modest than
   rev. 2 reported (R²≈0.15–0.19, not 0.327), and the true generalization
   estimate is noisy with this few time-ordered folds.
3. **GMP availability is not random** — reported GMP % skews toward
   stronger, earlier-listed issues. Any claim about GMP %'s predictive
   power implicitly applies to that subpopulation, not to IPOs in general.
4. **Still don't build a 6/12/24-month score from this dataset** — every
   model at every longer horizon has negative time-based CV R², worse than
   rev. 2's random-fold estimate suggested.
5. **Tuned regularization (RidgeCV/ElasticNetCV) clearly beats OLS
   out-of-sample on Model 4** (R²≈0.46–0.52 vs OLS's 0.150) — but
   ElasticNetCV's l1_ratio choice is unstable across folds, so treat this
   as "regularization helps here," not as a validated production model.
   Worth re-running once #1 (Model 3 vs 4) is resolved, since it was only
   checked against Model 4 so far.
6. **Always report time-based CV (or a real held-out future-IPO test),
   never random-fold CV alone** — the two disagree enough on this dataset
   (e.g. Model 3: 0.190 vs 0.581) that random-fold alone would have been
   actively misleading about how this would perform on genuinely future
   IPOs.
7. **Not done in this pass, flagged as the natural follow-up**: a
   robustness sweep (performance by time period, sector, IPO size, market
   regime), and re-running the now-tuned regularization comparison against
   Model 3 as well as Model 4. Both are more useful once a leading spec is
   picked from #1 above, not before.
