# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "scipy", "scikit-learn", "statsmodels"]
# ///
"""
Step 1: correlate each candidate scoring parameter against each candidate
target individually - a cheap sanity check. Reports Spearman rank
correlation alongside Pearson r; a much larger Spearman than Pearson for the
same pair is evidence the relationship isn't linear, which Pearson r/an OLS
coefficient alone can't tell you (see FINDINGS.md).

Step 2: OLS (via statsmodels, not sklearn) for real inference - coefficients,
std errors, t-stats, p-values, confidence intervals - plus VIF (with an
explicit constant column, per statsmodels' convention) to flag
multicollinearity. Every model is cross-validated two ways: **time-based**
(`TimeSeriesSplit` on rows sorted by `List Date` - the primary metric, since
a scoring model has to predict *future* IPOs from past ones, not interpolate
randomly shuffled ones) and random 5-fold (kept as a secondary benchmark).
Both report R^2, MAE and RMSE next to a `DummyRegressor(mean)` baseline, so
a negative or near-zero R^2 has something concrete to be worse/better than
(in-sample R^2 alone is not evidence of predictive power - see FINDINGS.md).

Demand is fit as two ALTERNATIVE model specs (Model A: Total Sub only,
Model B: QIB/NII/Retail), never combined - they're severely collinear with
each other. Allocation percentages (% QIB/% Retail/% anchor) are checked
for VIF only, never fed into a regression - they're SEBI-mandated
allocation-category templates (a handful of fixed values per issue type),
not a per-company signal, and % QIB/% anchor are literally r=1.000.

`matched_sample_comparison` re-fits the no-GMP vs +GMP % specs on the exact
same rows, so any apparent GMP benefit isn't confounded with the sample-size
drop that comes from restricting to the ~half of rows where GMP is known.
`gmp_missingness_check` tests whether that missing half is systematically
different (Listing Gain, subscription, sector, year) from the half that
reports GMP - if it is, "GMP % is the strongest signal" only holds for
whatever subpopulation actually reports it.

Once a leading spec is picked, `compare_regularization` checks OLS against
`RidgeCV`/`ElasticNetCV` on the same folds - not because a coefficient sign
flip is itself evidence OLS is wrong, but because this is the right point
in the process to check, after the feature spec is settled rather than
instead of settling it. Each picks its own alpha (and l1_ratio, for
ElasticNet) via its own internal CV on the training fold only, so tuning
never sees the outer time-based test fold.

Loops over four candidate targets (Listing Gain, and 6/12/24-month returns)
since it's an open question whether fundamentals matter more over a longer
horizon than they do for the listing-day pop.

Run: uv run analyze.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import chi2_contingency, mannwhitneyu, pearsonr, spearmanr
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import ElasticNetCV, LinearRegression, RidgeCV
from sklearn.model_selection import KFold, TimeSeriesSplit, cross_validate
from sklearn.preprocessing import StandardScaler
from statsmodels.stats.outliers_influence import variance_inflation_factor

DATASET = Path(__file__).parent / "ipo_dataset_3yr.csv"
TARGETS = ["Listing Gain", "Ret 6m %", "Ret 12m %", "Ret 24m %"]

FUNDAMENTALS = ["ROE", "ROCE", "D/E", "PAT Margin", "P/B", "Post IPO P/E", "EBITA Margin"]
DEMAND_TOTAL = ["Total Sub"]                # Model A: overall demand
DEMAND_CATEGORY = ["QIB", "NII", "Retail"]  # Model B: demand by investor category
# SEBI-mandated allocation-category percentages - exploratory VIF check
# only, never in a regression (see module docstring).
ALLOCATION = ["% QIB", "% Retail", "% anchor"]
# Normalized GMP - scale-invariant, much stronger signal than raw GMP
# (r~0.81 vs r~0.42): a fixed-rupee GMP means very different things for a
# cheap vs expensive issue. Mathematically identical to
# (Estimated Price - Offer Price) / Offer Price * 100 in this dataset
# (verified r=1.000 against that computation) - only one name is kept.
MARKET_SENTIMENT = ["GMP %"]

CV_SCORING = ["r2", "neg_mean_absolute_error", "neg_root_mean_squared_error"]


def add_derived_columns(df):
    df["GMP %"] = np.where(df["Offer Price"].gt(0), df["GMP"] / df["Offer Price"] * 100, np.nan)
    df["list_year"] = df["List Date"].dt.year
    return df


def correlations(df, cols, target):
    rows = []
    for col in cols:
        pair = df[[col, target]].dropna()
        if len(pair) < 10:
            continue
        r, p = pearsonr(pair[col], pair[target])
        rho, _ = spearmanr(pair[col], pair[target])
        rows.append({"parameter": col, "n": len(pair), "pearson_r": r, "p_value": p, "spearman_r": rho})
    return pd.DataFrame(rows).sort_values("pearson_r", key=abs, ascending=False)


def vif_table(X_df):
    """Variance Inflation Factor per predictor - >5 is commonly flagged as
    concerning collinearity, >10 as severe. Adds an explicit constant column
    for the auxiliary regressions per statsmodels' VIF convention, then
    drops it from the reported table - VIF is undefined/misleading for an
    intercept term itself."""
    X_const = sm.add_constant(X_df)
    vifs = [
        {"parameter": col, "VIF": variance_inflation_factor(X_const.values, i)}
        for i, col in enumerate(X_const.columns) if col != "const"
    ]
    return pd.DataFrame(vifs).sort_values("VIF", ascending=False)


def _n_splits(n):
    """Small-sample CV guard: skip below 20 rows, cap at 5 folds otherwise
    (the 24-month target has as few as 51 complete rows)."""
    return min(5, n // 10) if n >= 20 else 0


def _build_matrix(df, cols, target, restrict_index=None):
    subset = df[cols + [target, "List Date"]].dropna(subset=cols + [target])
    if restrict_index is not None:
        subset = subset.loc[subset.index.intersection(restrict_index)]
    X = subset[cols]
    y = subset[target].values
    X_scaled = pd.DataFrame(StandardScaler().fit_transform(X), columns=cols, index=X.index)
    time_order = subset["List Date"].argsort().values
    return subset, X_scaled, y, time_order


def _report_cv(X_scaled, y, time_order, n_splits):
    """Time-based (primary) and random 5-fold (secondary) CV, model vs a
    predict-the-mean baseline, R^2/MAE/RMSE for each."""
    for cv_name, cv, X_cv, y_cv in [
        ("time-based, primary", TimeSeriesSplit(n_splits=n_splits), X_scaled.values[time_order], y[time_order]),
        ("random 5-fold, secondary", KFold(n_splits=n_splits, shuffle=True, random_state=0), X_scaled.values, y),
    ]:
        for est_name, est in [("model", LinearRegression()), ("baseline (predict mean)", DummyRegressor())]:
            scores = cross_validate(est, X_cv, y_cv, cv=cv, scoring=CV_SCORING)
            r2, mae, rmse = scores["test_r2"], -scores["test_neg_mean_absolute_error"], -scores["test_neg_root_mean_squared_error"]
            print(f"  [{cv_name}] {est_name}: R^2={r2.mean():.3f} (range=[{r2.min():.3f}, {r2.max():.3f}]), "
                  f"MAE={mae.mean():.2f}, RMSE={rmse.mean():.2f}")


def regression(df, cols, label, target, restrict_index=None):
    subset, X_scaled, y, time_order = _build_matrix(df, cols, target, restrict_index)
    print(f"\n--- {label} -> {target} (n={len(subset)} complete rows) ---")
    if len(subset) < 20:
        print("too few complete rows to fit meaningfully, skipping")
        return

    # in-sample fit, for reference only - NOT a predictive-power estimate
    ols = sm.OLS(y, sm.add_constant(X_scaled)).fit()
    print(f"in-sample R^2 = {ols.rsquared:.3f}, adj R^2 = {ols.rsquared_adj:.3f}")

    n_splits = _n_splits(len(subset))
    if n_splits >= 2:
        _report_cv(X_scaled, y, time_order, n_splits)
    else:
        print("too few rows for cross-validation")

    # coefficient table with real inference stats (drop the constant row)
    summary = pd.DataFrame({
        "coef": ols.params,
        "std_err": ols.bse,
        "t": ols.tvalues,
        "p_value": ols.pvalues,
        "ci_low": ols.conf_int()[0],
        "ci_high": ols.conf_int()[1],
    }).drop("const")
    summary["significant_p<0.05"] = summary["p_value"] < 0.05
    print(summary.round(3).to_string())

    # relative abs. standardized coefficient share - descriptive only, NOT
    # a claim about variance explained or causal contribution
    share = 100 * summary["coef"].abs() / summary["coef"].abs().sum()
    print("\nrelative abs. standardized coefficient share (descriptive, not a statistical weight):")
    print(share.round(1).sort_values(ascending=False).to_string())

    if len(cols) >= 2:
        print("\nVIF (>5 = notable collinearity, >10 = severe):")
        print(vif_table(X_scaled).round(1).to_string(index=False))


def matched_sample_comparison(df, target):
    """Same rows for both specs, so a GMP-% benefit isn't confounded with
    the sample-size drop that comes from restricting to rows where GMP is
    known (~147/294)."""
    cols_with_gmp = FUNDAMENTALS + DEMAND_TOTAL + MARKET_SENTIMENT
    matched_idx = df[cols_with_gmp + [target]].dropna().index
    print(f"\n=== Matched-sample comparison (n={len(matched_idx)}, identical rows both specs) ===")
    if len(matched_idx) < 20:
        print("too few matched rows, skipping")
        return
    regression(df, FUNDAMENTALS + DEMAND_TOTAL, "Matched sample: Model A, no GMP %", target, restrict_index=matched_idx)
    regression(df, cols_with_gmp, "Matched sample: Model A + GMP %", target, restrict_index=matched_idx)


def compare_regularization(df, cols, label, target, restrict_index=None):
    """Step 8 (only run once a spec looks settled): OLS vs tuned Ridge vs
    tuned ElasticNet on the same features/folds. `RidgeCV`/`ElasticNetCV`
    each pick their own alpha (and l1_ratio) via their own internal CV,
    fit only on the outer training fold - so tuning never sees the outer
    time-based test fold. A coefficient sign flip in OLS isn't itself
    evidence OLS is wrong - this checks out-of-sample performance
    directly instead of guessing from coefficient shape.
    # ponytail: the inner tuning CV is plain KFold (sklearn's default for
    # RidgeCV/ElasticNetCV), not time-aware; fine for picking a knob, would
    # need a time-aware inner split too if this becomes the production model.
    """
    subset, X_scaled, y, time_order = _build_matrix(df, cols, target, restrict_index)
    print(f"\n=== Regularization comparison: {label} -> {target} (n={len(subset)}) ===")
    n_splits = _n_splits(len(subset))
    if n_splits < 2:
        print("too few rows, skipping")
        return
    X_time, y_time = X_scaled.values[time_order], y[time_order]
    alphas = np.logspace(-3, 3, 25)
    for est_name, est in [
        ("OLS", LinearRegression()),
        ("RidgeCV", RidgeCV(alphas=alphas)),
        ("ElasticNetCV", ElasticNetCV(alphas=alphas, l1_ratio=[.1, .3, .5, .7, .9, .95, 1.0], max_iter=5000)),
    ]:
        scores = cross_validate(
            est, X_time, y_time, cv=TimeSeriesSplit(n_splits=n_splits),
            scoring=CV_SCORING, return_estimator=True,
        )
        r2, mae, rmse = scores["test_r2"], -scores["test_neg_mean_absolute_error"], -scores["test_neg_root_mean_squared_error"]
        line = f"  {est_name}: time-based CV R^2={r2.mean():.3f}, MAE={mae.mean():.2f}, RMSE={rmse.mean():.2f}"
        if est_name == "RidgeCV":
            line += f" (alpha per fold: {[round(e.alpha_, 4) for e in scores['estimator']]})"
        elif est_name == "ElasticNetCV":
            line += (" (alpha, l1_ratio per fold: "
                      f"{[(round(float(e.alpha_), 4), float(e.l1_ratio_)) for e in scores['estimator']]})")
        print(line)


def allocation_vif_check(df):
    """ALLOCATION is exploratory-only - report VIF, never regress with it."""
    subset = df[ALLOCATION].dropna()
    print(f"\n--- Model C (exploratory only): allocation percentages (n={len(subset)}) ---")
    X_scaled = pd.DataFrame(StandardScaler().fit_transform(subset), columns=ALLOCATION, index=subset.index)
    print(vif_table(X_scaled).round(1).to_string(index=False))


def gmp_missingness_check(df):
    """Is the ~half of rows missing GMP % systematically different from the
    half that has it? If so, "GMP % is the strongest signal" only holds for
    whatever subpopulation actually reports GMP - see FINDINGS.md."""
    print(f"\n{'=' * 70}\nGMP MISSINGNESS CHECK\n{'=' * 70}")
    available = df["GMP"].notna()
    print(f"GMP available: {available.sum()}/{len(df)} rows")

    for col in ["Listing Gain", "Total Sub", "Offer Price", "list_year"]:
        a, b = df.loc[available, col].dropna(), df.loc[~available, col].dropna()
        if len(a) < 5 or len(b) < 5:
            continue
        stat, p = mannwhitneyu(a, b)
        print(f"  {col}: median available={a.median():.2f} vs missing={b.median():.2f} "
              f"(Mann-Whitney p={p:.4f})")

    if "Sector" in df.columns:
        table = pd.crosstab(df["Sector"], available)
        if table.shape[0] > 1 and (table.values > 0).all():
            chi2, p, _, _ = chi2_contingency(table)
            print(f"  Sector x GMP-availability: chi2 p={p:.4f}")
        else:
            print("  Sector x GMP-availability: skipped (sparse crosstab)")


MIN_SLICE_N = 15  # below this, a correlation estimate is too unstable to report


def _univariate_gmp_check(pair, label):
    """pair: two columns, [GMP %, target]. Prints Pearson r + a 2-parameter
    OLS (intercept + GMP %) - the full 8-predictor Model 3 needs far more
    rows than any of these subgroups have."""
    n = len(pair)
    if n < MIN_SLICE_N:
        print(f"  {label}: n={n}, too few rows")
        return
    r, p = pearsonr(pair.iloc[:, 0], pair.iloc[:, 1])
    ols = sm.OLS(pair.iloc[:, 1], sm.add_constant(pair.iloc[:, 0])).fit()
    slope, slope_p = ols.params.iloc[1], ols.pvalues.iloc[1]
    print(f"  {label}: n={n}, pearson_r={r:.3f}, GMP% slope={slope:.3f} (p={slope_p:.4f})")


def robustness_sweep(df, target="Listing Gain"):
    """Does the core GMP % -> target relationship Model 3 rests on hold up
    across time period / sector / IPO size, or only in aggregate? Checks
    the univariate relationship per subgroup, not the full multi-feature
    model - none of these subgroups are close to the ~80+ rows the full
    8-predictor Model 3 would need to fit meaningfully. Market regime is
    NOT checked here - this dataset has no market-index data (e.g. NIFTY)
    to derive bull/neutral/weak periods from; faking a proxy from
    list_year would just duplicate the time-period slice below while
    implying regime was accounted for when it wasn't - see FINDINGS.md."""
    base = df[FUNDAMENTALS + MARKET_SENTIMENT + [target, "list_year", "Sector", "Offer Price"]].dropna(
        subset=FUNDAMENTALS + MARKET_SENTIMENT + [target]
    )
    print(f"\n{'=' * 70}\nROBUSTNESS SWEEP: Model 3's GMP % relationship -> {target} (n={len(base)})\n{'=' * 70}")
    print("(market regime not checked - no market-index data in this dataset)")

    print("\n-- by year --")
    for year, group in base.groupby("list_year"):
        _univariate_gmp_check(group[["GMP %", target]], str(year))

    print("\n-- by sector --")
    for sector, group in base.groupby("Sector"):
        _univariate_gmp_check(group[["GMP %", target]], sector)

    print("\n-- by IPO size (Offer Price tercile, no total-issue-size column available) --")
    base = base.copy()
    base["offer_price_tercile"] = pd.qcut(base["Offer Price"], 3, labels=["small", "mid", "large"])
    for tercile, group in base.groupby("offer_price_tercile"):
        _univariate_gmp_check(group[["GMP %", target]], tercile)


def main():
    df = pd.read_csv(DATASET, parse_dates=["List Date"])
    df = add_derived_columns(df)
    all_params = FUNDAMENTALS + DEMAND_TOTAL + DEMAND_CATEGORY + ALLOCATION + MARKET_SENTIMENT

    allocation_vif_check(df)
    gmp_missingness_check(df)

    for target in TARGETS:
        print(f"\n{'=' * 70}\nTARGET: {target}\n{'=' * 70}")
        print(f"\n{'parameter':<16} {'n':>5} {'pearson r':>10} {'spearman r':>11} {'p-value':>10}")
        corr_df = correlations(df, all_params, target)
        for _, row in corr_df.iterrows():
            print(f"{row['parameter']:<16} {int(row['n']):>5} {row['pearson_r']:>10.3f} "
                  f"{row['spearman_r']:>11.3f} {row['p_value']:>10.4f}")

        regression(df, MARKET_SENTIMENT, "Model 0: GMP % only, no fundamentals gate (the frozen production formula)", target)
        regression(df, FUNDAMENTALS, "Model 1: fundamentals only", target)
        regression(df, FUNDAMENTALS + DEMAND_TOTAL, "Model 2: fundamentals + Total Sub (no GMP)", target)
        regression(df, FUNDAMENTALS + MARKET_SENTIMENT, "Model 3: fundamentals + GMP % (no Total Sub)", target)
        regression(df, FUNDAMENTALS + DEMAND_TOTAL + MARKET_SENTIMENT, "Model 4: fundamentals + Total Sub + GMP %", target)
        regression(df, FUNDAMENTALS + DEMAND_CATEGORY, "Model 5: fundamentals + QIB/NII/Retail (no GMP)", target)
        regression(df, FUNDAMENTALS + DEMAND_CATEGORY + MARKET_SENTIMENT, "Model 5b: fundamentals + QIB/NII/Retail + GMP %", target)

        matched_sample_comparison(df, target)

    # Model 3 beat Model 4 under both CV metrics in the model hierarchy
    # above - check regularization against it too, not just Model 4, before
    # treating either as the leading spec (see FINDINGS.md).
    compare_regularization(df, FUNDAMENTALS + MARKET_SENTIMENT, "Model 3", "Listing Gain")

    matched_idx = df[FUNDAMENTALS + DEMAND_TOTAL + MARKET_SENTIMENT + ["Listing Gain"]].dropna().index
    compare_regularization(
        df, FUNDAMENTALS + DEMAND_TOTAL + MARKET_SENTIMENT,
        "Model 4, matched sample", "Listing Gain", restrict_index=matched_idx,
    )

    robustness_sweep(df)


if __name__ == "__main__":
    main()
