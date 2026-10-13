"""Statistics tests.

Reference values are the standard published Wilson/Clopper-Pearson numbers for
these counts, plus invariants that must hold for every input.
"""

from __future__ import annotations

import sys

import pytest

from jev_harness.core.stats import (
    MissingStatsExtraError,
    brier_score,
    clopper_pearson,
    compare_rates,
    difference_interval,
    format_rate,
    holm_bonferroni,
    sample_warning,
    too_few_samples,
    two_proportion_p_value,
    upper_bound,
    wilson,
)

# ---------------------------------------------------------------------- Wilson


def test_wilson_reference_values() -> None:
    zero = wilson(0, 30)
    assert zero.estimate == 0.0
    assert zero.high == pytest.approx(0.1135, abs=5e-4)
    assert zero.low == 0.0
    assert zero.method == "wilson"

    half = wilson(15, 30)
    assert half.estimate == pytest.approx(0.5)
    assert half.low == pytest.approx(0.3315, abs=1e-3)
    assert half.high == pytest.approx(0.6685, abs=1e-3)

    everything = wilson(30, 30)
    assert everything.high == 1.0
    assert everything.low == pytest.approx(0.8865, abs=5e-4)


def test_wilson_brackets_the_point_estimate() -> None:
    for n in (1, 2, 5, 30, 97, 1000):
        for k in range(n + 1):
            interval = wilson(k, n)
            assert 0.0 <= interval.low <= interval.estimate <= interval.high <= 1.0


def test_wilson_empty_sample_is_uninformative() -> None:
    interval = wilson(0, 0)
    assert (interval.n, interval.estimate, interval.low, interval.high) == (0, 0.0, 0.0, 1.0)


def test_wilson_narrows_as_samples_grow() -> None:
    widths = [wilson(n // 2, n).width for n in (10, 50, 500, 5000)]
    assert widths == sorted(widths, reverse=True)


def test_wilson_respects_confidence_level() -> None:
    ninety = wilson(50, 100, confidence=0.90)
    ninety_five = wilson(50, 100, confidence=0.95)
    ninety_nine = wilson(50, 100, confidence=0.99)
    assert ninety.width < ninety_five.width < ninety_nine.width


@pytest.mark.parametrize(
    ("successes", "n"),
    [(6, 5), (-1, 5), (0, -1)],
)
def test_wilson_rejects_impossible_counts(successes: int, n: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        wilson(successes, n)


def test_wilson_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        wilson(1, 10, confidence=1.0)


def test_interval_renders_estimate_interval_and_n() -> None:
    text = str(wilson(3, 40))
    assert "7.5%" in text
    assert "95% CI" in text
    assert "n=40" in text


# ------------------------------------------------------------ Clopper-Pearson


def test_clopper_pearson_reference_values() -> None:
    pytest.importorskip("scipy")
    zero = clopper_pearson(0, 30)
    assert zero.low == 0.0
    assert zero.high == pytest.approx(0.1157, abs=5e-4)
    assert zero.method == "clopper-pearson"

    everything = clopper_pearson(30, 30)
    assert everything.high == 1.0
    assert everything.low == pytest.approx(0.8843, abs=5e-4)


def test_clopper_pearson_is_wider_than_wilson() -> None:
    pytest.importorskip("scipy")
    for successes, n in ((0, 10), (3, 40), (25, 30)):
        exact = clopper_pearson(successes, n)
        approximate = wilson(successes, n)
        assert exact.width > approximate.width


def test_clopper_pearson_without_the_stats_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "scipy.stats", None)
    with pytest.raises(MissingStatsExtraError, match="stats extra"):
        clopper_pearson(1, 10)


def test_clopper_pearson_empty_sample() -> None:
    interval = clopper_pearson(0, 0)
    assert (interval.low, interval.high, interval.n) == (0.0, 1.0, 0)


# ------------------------------------------------------------- comparisons


def test_identical_rates_are_not_significant() -> None:
    comparison = compare_rates(10, 100, 10, 100)
    assert comparison.difference.estimate == 0.0
    assert comparison.p_value == 1.0
    assert comparison.difference.contains(0.0)
    assert not comparison.significant


def test_different_rates_are_significant_and_ordered() -> None:
    comparison = compare_rates(50, 100, 10, 100)
    assert comparison.difference.estimate == pytest.approx(0.4)
    assert comparison.p_value < 1e-6
    assert comparison.significant
    assert comparison.difference.low > 0.0
    assert "newcombe" in comparison.difference.method


def test_difference_interval_is_symmetric_under_swap() -> None:
    forward = difference_interval(50, 100, 10, 100)
    backward = difference_interval(10, 100, 50, 100)
    assert forward.estimate == pytest.approx(-backward.estimate)
    assert forward.low == pytest.approx(-backward.high)
    assert forward.high == pytest.approx(-backward.low)


def test_p_value_handles_degenerate_inputs() -> None:
    assert two_proportion_p_value(0, 0, 5, 10) == 1.0
    assert two_proportion_p_value(10, 10, 10, 10) == 1.0


def test_upper_bound_is_not_the_point_estimate() -> None:
    assert upper_bound(0, 30) == pytest.approx(0.1135, abs=5e-4)
    assert upper_bound(0, 30, exact=True) == pytest.approx(0.1157, abs=5e-4)
    assert upper_bound(0, 30) > 0 / 30


# ----------------------------------------------------------- multiplicity


def test_holm_bonferroni_step_down_example() -> None:
    results = holm_bonferroni([0.001, 0.02, 0.03, 0.04], alpha=0.05)
    assert [result.index for result in results] == [0, 1, 2, 3]
    assert [result.rejected for result in results] == [True, False, False, False]
    assert results[0].adjusted_alpha == pytest.approx(0.0125)
    assert results[1].adjusted_alpha == pytest.approx(0.05 / 3)


def test_holm_bonferroni_never_more_permissive_than_uncorrected() -> None:
    p_values = [0.011, 0.013, 0.021, 0.034, 0.049, 0.5, 0.9]
    results = holm_bonferroni(p_values, alpha=0.05)
    for result in results:
        if result.rejected:
            assert result.p_value <= 0.05


def test_holm_bonferroni_single_hypothesis_equals_uncorrected() -> None:
    results = holm_bonferroni([0.04], alpha=0.05)
    assert results[0].rejected
    assert results[0].adjusted_alpha == pytest.approx(0.05)


def test_holm_bonferroni_empty_input() -> None:
    assert holm_bonferroni([]) == []


def test_holm_bonferroni_validates_arguments() -> None:
    with pytest.raises(ValueError, match="p-values"):
        holm_bonferroni([1.5])
    with pytest.raises(ValueError, match="alpha"):
        holm_bonferroni([0.1], alpha=0.0)


# ------------------------------------------------------------------ metrics


def test_brier_score_reference_values() -> None:
    assert brier_score([(1.0, True), (0.0, False)]) == 0.0
    assert brier_score([(0.5, True), (0.5, False)]) == pytest.approx(0.25)
    assert brier_score([(0.9, False), (0.1, True)]) == pytest.approx(0.81)


def test_brier_score_validates_input() -> None:
    with pytest.raises(ValueError, match="at least one prediction"):
        brier_score([])
    with pytest.raises(ValueError, match="within"):
        brier_score([(1.4, True)])


def test_format_rate_is_consistent() -> None:
    assert format_rate(0.0) == "0.0%"
    assert format_rate(0.5) == "50.0%"
    assert format_rate(1.0) == "100.0%"


# ------------------------------------------------------- sample-size honesty


def test_sample_warning_for_small_samples() -> None:
    warning = sample_warning(5, 10)
    assert warning is not None
    assert "at least 30" in warning


def test_sample_warning_for_few_events() -> None:
    warning = sample_warning(1, 100)
    assert warning is not None
    assert "upper bound" in warning


def test_no_sample_warning_when_the_sample_supports_a_claim() -> None:
    assert sample_warning(50, 100) is None


def test_too_few_samples_boundary() -> None:
    assert too_few_samples(29)
    assert not too_few_samples(30)
    assert not too_few_samples(5000, min_samples=100)
