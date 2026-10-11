"""
The frozen Listing Gain scoring formula - see
docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md
for how these numbers were derived, and
`research/ipo-scoring-validation/analyze.py`'s "Model 0" for how to
reproduce them (`uv run analyze.py`).

Self-contained on purpose: no live data dependency, no DB. `run.py`'s
`send_pending_alert` is the live-wiring pass - it supplies a real GMP %
from `db.get_latest_gmp_pct` (populated by `gmp_source.py`'s poller).

Only targets Listing Gain. Every 6/12/24-month spec has negative
time-based CV R^2 (see `research/ipo-scoring-validation/FINDINGS.md`) and
must not be scored with this or any formula.
"""
from dataclasses import dataclass

# OLS fit on all 147 historical IPOs with known GMP % - see "Model 0" in
# analyze.py and the design doc's "The formula" section.
INTERCEPT = -0.2206
SLOPE = 1.0428

# 120 real out-of-sample (time-based CV) prediction errors, actual -
# predicted, from the same run - NOT a symmetric +-MAE band. See the
# design doc's "Output presentation" section for why a flat +-MAE band
# was wrong. Percentile -> error at that percentile.
ERROR_PERCENTILES = {
    5: -21.9,
    25: -8.2,
    50: -2.0,
    75: 10.3,
    95: 29.9,
}

DISCLAIMER = (
    "Based on GMP % (grey market premium - an unofficial, informal "
    "indicator; not exchange-verified). This is a statistical estimate, "
    "not advice."
)


@dataclass(frozen=True)
class Prediction:
    point: float  # predicted listing gain, %
    low: float    # 5th-percentile-of-past-errors bound, %
    high: float   # 95th-percentile-of-past-errors bound, %
    disclaimer: str = DISCLAIMER


def predict_listing_gain(gmp_pct: float) -> Prediction:
    """Predicts Listing Gain % from GMP %, plus the empirical (asymmetric)
    5th-95th percentile interval from past out-of-sample errors - not a
    symmetric +-MAE band, which would understate the real upside variance
    (see ERROR_PERCENTILES's docstring note above)."""
    point = INTERCEPT + SLOPE * gmp_pct
    return Prediction(
        point=point,
        low=point + ERROR_PERCENTILES[5],
        high=point + ERROR_PERCENTILES[95],
    )


def format_prediction(p: Prediction) -> str:
    """The exact wording the design doc's 'Output presentation' section
    specifies - point estimate, the asymmetric range with its caveat, and
    the disclaimer, never a bare number."""
    return (
        f"Predicted Listing Gain: {p.point:+.1f}%\n"
        f"Historically, actual outcomes for a similar prediction have ranged from "
        f"about {p.low:+.0f}% to {p.high:+.0f}% (5th-95th percentile of past "
        f"prediction errors) - the spread is NOT symmetric; big positive "
        f"surprises are more common than big negative ones.\n"
        f"{p.disclaimer}"
    )
