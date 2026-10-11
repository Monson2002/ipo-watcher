# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
The frozen Listing Gain scoring formula - see
docs/superpowers/specs/2026-09-20-listing-gain-scoring-formula-design.md
for how these numbers were derived, and `analyze.py`'s "Model 0" for how
to reproduce them (`uv run analyze.py`).

Self-contained on purpose: no live data dependency, no DB, nothing to
mock to test it. A later live-wiring pass imports `predict_listing_gain`
and supplies a real GMP % from whatever source gets chosen (see the
design doc - IPO Guru's free API, pending a real test call with a key).

Only targets Listing Gain. Every 6/12/24-month spec has negative
time-based CV R^2 (see FINDINGS.md) and must not be scored with this or
any formula.
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


def _demo():
    """ponytail-style runnable self-check: verifies the formula arithmetic
    and the interval ordering against hand-computed values, not just that
    it runs without raising."""
    p = predict_listing_gain(18.0)
    expected_point = -0.2206 + 1.0428 * 18.0
    assert abs(p.point - expected_point) < 1e-9, p
    assert abs(p.low - (expected_point - 21.9)) < 1e-9, p
    assert abs(p.high - (expected_point + 29.9)) < 1e-9, p
    assert p.low < p.point < p.high, p

    # a negative GMP % (IPO listed well below the grey-market price) must
    # still produce a coherent, ordered interval, not just a happy-path one
    p2 = predict_listing_gain(-5.0)
    assert p2.low < p2.point < p2.high, p2

    print(format_prediction(p))
    print("\nself-check passed")


if __name__ == "__main__":
    _demo()
