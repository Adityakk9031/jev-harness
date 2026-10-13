"""Exact answers from gates whose calibration is known by construction."""

from __future__ import annotations

import pytest
from tests.modules.gate_calibration.gen import Level, gate_rows

from jev_harness.core.stats import clopper_pearson
from jev_harness.modules.gate_calibration import metrics
from jev_harness.modules.gate_calibration.data import GateRow

pytest.importorskip("scipy")


def rows(levels: list[Level], **kwargs: object) -> list[GateRow]:
    return [GateRow.model_validate(r) for r in gate_rows(levels, **kwargs)]  # type: ignore[arg-type]


def test_a_perfectly_calibrated_gate_has_zero_error() -> None:
    data = rows([(0.65, 100, 65), (0.75, 100, 75), (0.85, 100, 85), (0.95, 100, 95)])
    assert metrics.expected_calibration_error(data, bins=10) == pytest.approx(0.0, abs=1e-9)
    table = metrics.reliability_table(data, bins=10)
    assert [round(b.accuracy, 2) for b in table] == [0.65, 0.75, 0.85, 0.95]
    assert all(b.n == 100 for b in table)


def test_an_overconfident_gate_has_a_positive_gap_equal_to_the_construction() -> None:
    data = rows([(0.95, 100, 60)])
    (only,) = metrics.reliability_table(data, bins=10)
    assert only.gap == pytest.approx(0.35)
    assert metrics.expected_calibration_error(data, bins=10) == pytest.approx(0.35)
    assert metrics.weighted_brier(data) == pytest.approx(0.6 * 0.05**2 + 0.4 * 0.95**2)


def test_an_underconfident_gate_has_a_negative_gap() -> None:
    data = rows([(0.65, 100, 90)])
    (only,) = metrics.reliability_table(data, bins=10)
    assert only.gap == pytest.approx(-0.25)


def test_confidence_one_falls_in_the_last_bin() -> None:
    data = rows([(1.0, 10, 10)])
    (only,) = metrics.reliability_table(data, bins=10)
    assert (only.low, only.high) == (0.9, 1.0)


def test_miss_rate_and_share_skipped_at_each_threshold() -> None:
    data = rows([(0.7, 200, 180), (0.9, 200, 196), (0.99, 400, 400)], run_decisions=200)
    table = {row.threshold: row for row in metrics.threshold_table(data, "skip")}
    assert set(table) == {0.7, 0.9, 0.99}
    assert table[0.99].misses.successes == 0
    assert table[0.99].misses.n == 400
    assert table[0.99].skipped_share == pytest.approx(400 / 1000)
    assert table[0.9].misses.successes == 4
    assert table[0.9].misses.n == 600
    assert table[0.9].skipped_share == pytest.approx(600 / 1000)
    assert table[0.7].misses.successes == 24
    assert table[0.7].misses.n == 800
    assert table[0.7].miss_rate == pytest.approx(0.03)
    assert table[0.9].upper_bound == pytest.approx(clopper_pearson(4, 600).high)
    assert table[0.9].bound_method == "clopper-pearson"
    # Risky items = the 24 skips labelled "run"; the 200 run decisions are labelled "run" too.
    assert table[0.7].risky_escape_rate == pytest.approx(24 / 224)


def test_recommendation_is_the_cheapest_threshold_meeting_the_target() -> None:
    data = rows([(0.7, 200, 180), (0.9, 200, 196), (0.99, 400, 400)], run_decisions=200)
    recommendation = metrics.recommend(metrics.threshold_table(data, "skip"), target=0.02)
    assert recommendation.threshold == 0.9  # skips 60%; 0.7 breaches the target, 0.99 skips less
    assert recommendation.row is not None
    assert recommendation.row.upper_bound <= 0.02


def test_no_misses_but_too_few_labels_asks_for_the_exact_number_more() -> None:
    data = rows([(0.99, 100, 100)])
    recommendation = metrics.recommend(metrics.threshold_table(data, "skip"), target=0.01)
    assert recommendation.threshold is None
    assert recommendation.additional_labels is not None
    needed = next(n for n in range(1, 2000) if clopper_pearson(0, n).high <= 0.01)
    assert recommendation.additional_labels == needed - 100
    assert metrics.labels_needed(0.0, 0.01) == needed


def test_a_gate_that_misses_more_than_the_target_gets_no_label_advice() -> None:
    data = rows([(0.95, 100, 90)])  # 10% misses
    recommendation = metrics.recommend(metrics.threshold_table(data, "skip"), target=0.01)
    assert recommendation.threshold is None
    assert recommendation.additional_labels is None
    assert "more labels will not help" in recommendation.reason
    assert metrics.labels_needed(0.10, 0.01) is None


def test_recommendation_with_nothing_labelled() -> None:
    data = rows([(0.9, 10, 10)], labeled=False)
    recommendation = metrics.recommend(metrics.threshold_table(data, "skip"), target=0.01)
    assert recommendation.threshold is None
    assert "no labelled skip" in recommendation.reason


def test_many_distinct_confidences_are_capped() -> None:
    data = [
        GateRow(id=str(i), decision="skip", confidence=i / 1000, label="skip") for i in range(1001)
    ]
    assert len(metrics.candidate_thresholds(data, "skip")) <= metrics.MAX_THRESHOLDS


def test_stratified_weights_change_the_estimate_and_the_effective_sample() -> None:
    heavy = GateRow(id="a", decision="skip", confidence=0.9, label="run", weight=9.0)
    light = [GateRow(id=f"b{i}", decision="skip", confidence=0.9, label="skip") for i in range(9)]
    count = metrics.weighted_count([heavy, *light], [True, *([False] * 9)])
    assert count.weighted is True
    assert count.estimate == pytest.approx(9 / 18)
    assert count.n < 10  # Kish effective sample size is below the raw row count


def test_drift_between_windows_with_holm_correction() -> None:
    before = rows([(0.85, 200, 170), (0.65, 200, 130)], prefix="a")
    stable = rows([(0.65, 200, 130)], prefix="b")
    drifted = rows([(0.85, 200, 120)], prefix="c")
    scan, skipped = metrics.drift_scan(before, stable + drifted, bins=5, min_per_window=10)
    by_name = {item.name: item for item in scan}
    assert by_name["confidence 0.80-1.00"].rejected is True
    assert by_name["confidence 0.60-0.80"].rejected is False
    assert skipped == []


def test_identical_windows_show_no_drift_and_tiny_bins_are_skipped() -> None:
    window = rows([(0.85, 200, 170), (0.45, 5, 3)])
    scan, skipped = metrics.drift_scan(window, window, bins=5, min_per_window=10)
    assert not any(item.rejected for item in scan)
    assert skipped == ["confidence 0.40-0.60 (n=5 vs 5)"]
