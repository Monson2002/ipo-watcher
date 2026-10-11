# Listing Gain Scoring Formula — Design

> Design doc for the scoring formula itself, not the live pipeline around it.
> Scope explicitly excludes DB schema, wiring into `ipo_watcher`'s live
> collector, and refresh cadence — see "Explicitly out of scope" below.

## Context

`research/ipo-scoring-validation/` (see `FINDINGS.md` rev. 3) spent three
revisions validating which of the original design doc's four candidate
parameters (fundamentals, GMP, subscription, sentiment) actually predict
Listing Gain. The answer turned out simpler than the original 4-parameter
idea assumed: **only `GMP %` is individually significant.** The 7
fundamentals never reached significance in any tested spec (Model 1, and
inside every combined model), and `Total Sub` — kept as a live candidate
through most of rev. 3 — was shown in this design session to actively hurt
performance once fundamentals stop gating the sample (R² drops from 0.650
to 0.500 when Total Sub is added; see "Why GMP % alone" below).

This doc turns that research conclusion into an actual formula: what it
computes, what it needs live, and how it's presented. Nothing here is
wired into the live collector yet — see the deferred-scope section.

## The formula

```
predicted_listing_gain_% = -0.2206 + 1.0428 × GMP_%
```

Plain OLS, fit on all 147 historical IPOs with known `GMP %` (no
fundamentals-completeness requirement — that was an artifact of
`analyze.py`'s Model 3 spec including fundamentals as predictors even
though they contributed nothing measurable).

**Validation** (via `analyze.py`'s existing `regression()`/`_report_cv()`
machinery — see `research/ipo-scoring-validation/FINDINGS.md` for the full
methodology): time-based CV (`TimeSeriesSplit`, the project's established
primary metric) gives **R²=0.650, MAE=12.26 percentage points, RMSE=16.55
points** — the strongest validated out-of-sample result across every spec
tested in this project.

**Why GMP % alone**, not GMP % + Total Sub:

| Spec | n | Time-based CV R² | MAE |
|---|---|---|---|
| GMP % alone | 147 | **0.650** | **12.26** |
| GMP % + Total Sub | 139 | 0.500 | 15.32 |

Total Sub was carried as a candidate "minor secondary factor" through most
of this design session, but this exact 2-variable spec had never actually
been tested until now (every prior spec either included fundamentals or
was GMP-%-only with fundamentals still gating the row filter). Once tested
on its own terms, adding Total Sub clearly hurts, not helps. Dropped.

**A notable property, not a coincidence worth over-reading**: the
intercept is ≈0 and the slope is ≈1 — `GMP %` is, on average, close to an
*unbiased* predictor of actual Listing Gain, not merely correlated with
it. This is a clean, explainable story ("the grey market premium is a
reasonable estimate of what you'll actually get") but shouldn't be
mistaken for more precision than the MAE (±12 points) actually supports.

**No regularization applied.** Ridge/ElasticNet clearly beat OLS on the
multi-predictor specs (Model 3, Model 4 — see `FINDINGS.md`) because they
control multicollinearity/overfitting across *correlated* predictors.
With a single predictor there's nothing to regularize against; plain OLS
is simpler and equally defensible.
`# ponytail: single-predictor OLS, no regularization — revisit only if a
second validated feature is ever added back.`

**Refit cadence**: not decided here — the coefficients above are a
snapshot fit on the current 3-year dataset. Whether/how often to refit as
more historical outcomes accumulate is deferred (see below).

## Live data source: IPO Guru's free API

Supplies `GMP %` per currently-tracked IPO — `GET /ipos` (optionally
filtered by `status=open|upcoming|closed`), `X-API-KEY` header auth, no
credit card. Response includes both GMP and subscription fields per IPO
Guru's docs, though only the `GMP %` field is needed given the decision
above.

**Correction, 2026-10-11**: the free tier is **10 requests/day, 1/min**
("Evaluation only"), not 300/day as originally stated here — that number
was never verified against the real pricing page. Confirmed directly from
`https://www.ipoguru.in/ipo-gmp-details-developer-api`. See
`docs/superpowers/specs/2026-10-11-gmp-poller-design.md` for the full tier
table and how the poller design accounts for this tighter budget (one
unfiltered call per poll, ~10 calls/day spread across market hours, no
retry headroom on a rate-limit error).

**Partially verified, 2026-10-11**: an unauthenticated `GET
https://www.ipoguru.in/api/v1/ipos` returns a real `401 Unauthorized` —
the endpoint exists and enforces the documented auth requirement, not a
dead link or a documentation fiction. That is the most that can be
checked without a key. Still open, and still blocking before this is
treated as fully settled:
- Exact field name/shape for GMP % in a real response (docs vs. reality)
  — needs an authenticated call, not just reachability.
- API key issuance actually works end to end — request a key by emailing
  `ipoguru.in[at]gmail.com` per their docs (a manual step; no automated
  path exists) and confirm turnaround and that a real key arrives.
- The 300/day free-tier limit is workable for however often the live
  collector ends up polling (cadence is a deferred decision, but the
  limit should be checked against whatever cadence gets chosen later).

**Why this over the cloud alternatives already researched** (Apify's
India IPO Tracker, parse.bot's InvestorGain API): both are paid cloud
APIs; `CLAUDE.md` states this project uses no cloud services anywhere,
by design. IPO Guru's free tier avoids that trade-off. A direct scrape
(a new `gmp_source.py`, matching `sebi_source.py`'s existing pattern) was
also considered and remains a fallback if IPO Guru's free API doesn't pan
out under real testing.

## Output presentation

**A flat "±MAE" band was the original plan here and is wrong — resolved
in this revision.** MAE (12.26pp) is an *average* magnitude of historical
error, not a bound: it doesn't mean every prediction lands within ±12 of
the truth, and presenting it with a "±" symbol reads exactly like a bound
even though it isn't one.

The actual out-of-sample error distribution (120 real historical
predictions, `actual − predicted`, computed via the same `TimeSeriesSplit`
folds `analyze.py` already uses) is asymmetric with a heavy right tail —
consistent with the OLS residual diagnostics already on record (skew=0.76,
kurtosis=8.2):

| Percentile | Actual − Predicted |
|---|---|
| 5th | −21.9 |
| 25th | −8.2 |
| 50th (median) | −2.0 |
| 75th | +10.3 |
| 95th | +29.9 |
| max overshoot | **+74.6** |
| max undershoot | −37.6 |

Real listing days blow past the GMP-implied prediction on the upside far
more dramatically than they undershoot on the downside. So the output
should present an **empirical, asymmetric interval** derived from this
residual distribution (5th/95th percentile, refit alongside the formula
itself), not a symmetric ± band:

```
Predicted Listing Gain: +18.4%
Historically, actual outcomes for a similar prediction have ranged from
about -4% to +48% (5th-95th percentile of past prediction errors) - the
spread is NOT symmetric; big positive surprises are more common than big
negative ones.
Based on GMP % (grey market premium — an unofficial, informal indicator;
not exchange-verified). This is a statistical estimate, not advice.
```

- This resolves the "flat ±MAE vs. proper interval" open question from
  the previous revision of this doc in favor of the empirical/asymmetric
  interval — it's not meaningfully harder to compute (already done above)
  and a symmetric band would actively understate upside variance.
- The disclaimer is not an optional extra — cheapest defensibility move
  available for a published, GMP-derived number, matching the original
  design doc's own reasoning for disclosing GMP's informal nature.
- **When an IPO has no `GMP %` yet** (common before its bidding window
  opens, or if IPO Guru simply hasn't picked it up) — say so plainly
  ("GMP not yet available") rather than silently omitting the IPO or
  showing a stale/blank number.

## Explicitly out of scope (deferred to later, separate design passes)

- **New DB schema** to store GMP snapshots (a `market_signals`-shaped
  table was sketched in the original Phase 2 ideation doc, but its shape
  should be revisited now that only one field — `GMP %` — is actually
  needed, not four).
- **Wiring the score into `ipo_watcher`'s live collector/email** — this
  doc defines the formula and its data source, not how `run.py` calls it.
- **When in an IPO's lifecycle to compute/refresh the score** — GMP moves
  over time right up to listing day; compute once at filing discovery,
  or re-check closer to listing? Needs its own decision, informed by
  whatever the DB schema pass above settles on.
- **Formula refit cadence** — retrain as new historical outcomes
  accumulate, on what schedule, with what regression-testing against the
  numbers in this doc.

## What this spec asks to be implemented next

Concrete, buildable now, without waiting on the deferred DB/wiring
decisions:

1. ~~**Formalize the GMP-%-only spec in `analyze.py`/`FINDINGS.md`**~~ —
   **done, 2026-10-11.** Added as "Model 0" in `analyze.py`, right before
   Model 1. `uv run analyze.py` now reproduces this doc's numbers exactly
   (R²=0.650, MAE=12.26, RMSE=16.55, n=147).
2. ~~**A small, pure `predict_listing_gain(gmp_pct)` function**~~ —
   **done, 2026-10-11.** `ipo_watcher/scoring.py` — frozen coefficients,
   the empirical percentile interval, the exact output wording from this
   doc, and `tests/test_scoring.py`. Originally written at
   `research/ipo-scoring-validation/score.py`; moved into the live
   package once `run.py` actually wired it into the email alert (see
   item 4 below) — `research/` isn't an importable package and is meant
   to stay the validation workspace, not live code.
3. **Verify IPO Guru's API with a real test call** — **partially done,
   2026-10-11**: confirmed the endpoint is real (401 on an unauthenticated
   call, not a dead link). Still needs an actual key, which only a human
   can request by email — see the updated "Live data source" section
   above. Not something Claude can complete unassisted.

4. ~~**Wire the score into the live collector's email alert**~~ — **done,
   2026-10-11.** `run.py`'s `send_pending_alert` calls
   `db.get_latest_gmp_pct` per filing's `company_id`, then
   `scoring.predict_listing_gain`/`format_prediction` when a snapshot
   exists, or the literal `"GMP not yet available."` otherwise — per this
   doc's own "Output presentation" instruction to say so plainly, never a
   blank/stale number. Applies to every row, including corrigenda (GMP
   may have moved since the original filing). See
   `tests/test_run.py::test_send_pending_alert_includes_predicted_gain_when_gmp_available`.

Still deferred: refresh cadence (when in an IPO's lifecycle to
recompute), refit cadence (retraining the formula itself as more outcomes
accumulate).

## Open questions for the next pass

1. IPO Guru's free API needs an actual verified test call (see above) —
   who does this, and what's the fallback if it doesn't check out
   (direct scrape, per the fallback noted above)?
2. This formula only ever targets **Listing Gain** — per `FINDINGS.md`,
   every 6/12/24-month spec has negative time-based CV R² and shouldn't be
   scored at all. Worth restating explicitly wherever this formula is
   eventually surfaced, so nobody assumes it says anything about
   medium-term returns.
