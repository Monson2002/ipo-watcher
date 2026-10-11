from ipo_watcher.scoring import predict_listing_gain, format_prediction


def test_predict_listing_gain_matches_the_frozen_formula():
    p = predict_listing_gain(18.0)
    expected_point = -0.2206 + 1.0428 * 18.0
    assert abs(p.point - expected_point) < 1e-9
    assert abs(p.low - (expected_point - 21.9)) < 1e-9
    assert abs(p.high - (expected_point + 29.9)) < 1e-9
    assert p.low < p.point < p.high


def test_predict_listing_gain_handles_negative_gmp():
    p = predict_listing_gain(-5.0)
    assert p.low < p.point < p.high


def test_format_prediction_includes_point_range_and_disclaimer():
    p = predict_listing_gain(18.0)
    text = format_prediction(p)
    assert "Predicted Listing Gain: +18.5%" in text
    assert "5th-95th percentile" in text
    assert p.disclaimer in text
