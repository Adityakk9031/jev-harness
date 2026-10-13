"""Interval estimation, sample-size honesty, and multiplicity control.

Every number this package reports is a proportion measured from repeated
trials, and agents are non-deterministic, so a bare point estimate is not
allowed to reach a report (brief 2.7). This module is pure standard library:

* ``wilson`` - Wilson score interval, closed form.
* ``clopper_pearson`` - exact interval; needs the optional ``[stats]`` extra.
* ``difference_interval`` - Newcombe hybrid-score interval for a difference of
  proportions, also closed form.
* ``two_proportion_p_value`` - pooled score test, used for drift.
* ``holm_bonferroni`` - multiplicity control for a threshold scan.
* ``brier_score`` - a calibration metric for the gate module.

Design note (PLAN D2): only Clopper-Pearson needs scipy, and it raises
:class:`MissingStatsExtraError` rather than silently falling back to a
method under the same name. Methods are labelled in :class:`Interval.method` so
a report can never confuse an exact interval with an approximate one.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import NormalDist

#: Below this many samples, a rate is reported as provisional.
DEFAULT_MIN_SAMPLES = 30
#: Below this many observed events, an upper bound cannot distinguish a small
#: true rate from zero, so the interval (not the point estimate) is the story.
DEFAULT_MIN_EVENTS = 5


class MissingStatsExtraError(RuntimeError):
    """An exact method was requested without the optional ``[stats]`` extra."""

    def __init__(self, method: str) -> None:
        super().__init__(
            f"{method} needs the optional stats extra: "
            "install with `uv pip install 'jev-harness[stats]'` or "
            "`pip install 'jev-harness[stats]'`"
        )


def format_rate(value: float) -> str:
    """Format a proportion for humans and reports, always the same way."""
    return f"{value * 100:.1f}%"


@dataclass(frozen=True, slots=True)
class Interval:
    """A point estimate with an interval and the sample size behind it."""

    estimate: float
    low: float
    high: float
    n: int
    confidence: float = 0.95
    method: str = "wilson"

    @property
    def width(self) -> float:
        return self.high - self.low

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def __str__(self) -> str:
        level = f"{self.confidence * 100:.0f}%"
        return (
            f"{format_rate(self.estimate)} "
            f"({level} CI {format_rate(self.low)} to {format_rate(self.high)}, "
            f"n={self.n})"
        )


@dataclass(frozen=True, slots=True)
class RateComparison:
    """Two rates compared, with the interval on their difference."""

    a: Interval
    b: Interval
    difference: Interval
    p_value: float

    @property
    def significant(self) -> bool:
        """Whether the difference is significant at the intervals' confidence."""
        return self.p_value < (1.0 - self.a.confidence)


@dataclass(frozen=True, slots=True)
class HolmResult:
    """One hypothesis in a Holm-Bonferroni step-down procedure."""

    index: int
    p_value: float
    adjusted_alpha: float
    rejected: bool


def _validate_counts(successes: int, n: int) -> None:
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    if successes < 0 or successes > n:
        raise ValueError(f"successes must be within [0, n], got {successes} of {n}")


def _z(confidence: float) -> float:
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be strictly between 0 and 1, got {confidence}")
    return NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


#: When a bound is analytically at 0 or 1 (for example the lower bound of 0 of
#: n successes), floating point arithmetic can leave a residue of ~1e-16.
#: Reporting a lower bound of 0.0000000000000139% as if it were a measurement
#: would be noise dressed up as precision, so bounds within this tolerance of a
#: hard limit are snapped to it.
_BOUND_TOLERANCE = 1e-12


def _snap_to_bound(value: float, bound: float) -> float:
    if abs(value - bound) <= _BOUND_TOLERANCE:
        return bound
    return min(max(value, 0.0), 1.0) if bound in (0.0, 1.0) else value


def _empty_interval(confidence: float, method: str) -> Interval:
    """With no samples every value is possible; say so instead of guessing."""
    return Interval(estimate=0.0, low=0.0, high=1.0, n=0, confidence=confidence, method=method)


def wilson(successes: int, n: int, confidence: float = 0.95) -> Interval:
    """Wilson score interval for a proportion (closed form, safe for small n)."""
    _validate_counts(successes, n)
    if n == 0:
        return _empty_interval(confidence, "wilson")

    z = _z(confidence)
    z2 = z * z
    p = successes / n
    denominator = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denominator
    return Interval(
        estimate=p,
        low=_snap_to_bound(center - half, 0.0),
        high=_snap_to_bound(center + half, 1.0),
        n=n,
        confidence=confidence,
        method="wilson",
    )


def clopper_pearson(successes: int, n: int, confidence: float = 0.95) -> Interval:
    """Exact (Clopper-Pearson) interval for a proportion. Requires ``[stats]``."""
    _validate_counts(successes, n)
    if n == 0:
        return _empty_interval(confidence, "clopper-pearson")

    try:
        from scipy.stats import beta
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch test
        raise MissingStatsExtraError("Clopper-Pearson intervals") from exc

    alpha = 1.0 - confidence
    p = successes / n
    low = 0.0 if successes == 0 else float(beta.ppf(alpha / 2.0, successes, n - successes + 1))
    high = (
        1.0 if successes == n else float(beta.ppf(1.0 - alpha / 2.0, successes + 1, n - successes))
    )
    return Interval(
        estimate=p,
        low=low,
        high=high,
        n=n,
        confidence=confidence,
        method="clopper-pearson",
    )


def upper_bound(successes: int, n: int, confidence: float = 0.95, *, exact: bool = False) -> float:
    """Upper confidence bound on a rate - the number that matters for misses.

    For a miss rate the question is never "how often did it miss" but "how bad
    could it be", which is the upper bound, not the point estimate.
    """
    interval = (
        clopper_pearson(successes, n, confidence) if exact else wilson(successes, n, confidence)
    )
    return interval.high


def difference_interval(
    successes_a: int,
    n_a: int,
    successes_b: int,
    n_b: int,
    confidence: float = 0.95,
) -> Interval:
    """Newcombe hybrid-score interval for ``rate_a - rate_b``.

    ``Interval.n`` is the combined sample size, since both groups feed the
    comparison; the per-group sizes stay available on the group intervals.
    """
    a = wilson(successes_a, n_a, confidence)
    b = wilson(successes_b, n_b, confidence)
    diff = a.estimate - b.estimate
    low = diff - math.sqrt((a.estimate - a.low) ** 2 + (b.high - b.estimate) ** 2)
    high = diff + math.sqrt((a.high - a.estimate) ** 2 + (b.estimate - b.low) ** 2)
    return Interval(
        estimate=diff,
        low=max(-1.0, low),
        high=min(1.0, high),
        n=n_a + n_b,
        confidence=confidence,
        method="newcombe",
    )


def two_proportion_p_value(successes_a: int, n_a: int, successes_b: int, n_b: int) -> float:
    """Two-sided p-value from the pooled two-proportion score test."""
    _validate_counts(successes_a, n_a)
    _validate_counts(successes_b, n_b)
    if n_a == 0 or n_b == 0:
        return 1.0

    pooled = (successes_a + successes_b) / (n_a + n_b)
    standard_error = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n_a + 1.0 / n_b))
    if standard_error == 0.0:
        return 1.0
    z = (successes_a / n_a - successes_b / n_b) / standard_error
    return float(2.0 * (1.0 - NormalDist().cdf(abs(z))))


def compare_rates(
    successes_a: int,
    n_a: int,
    successes_b: int,
    n_b: int,
    confidence: float = 0.95,
) -> RateComparison:
    """Compare two rates: point estimates, their difference, and a p-value."""
    return RateComparison(
        a=wilson(successes_a, n_a, confidence),
        b=wilson(successes_b, n_b, confidence),
        difference=difference_interval(successes_a, n_a, successes_b, n_b, confidence),
        p_value=two_proportion_p_value(successes_a, n_a, successes_b, n_b),
    )


def holm_bonferroni(p_values: Sequence[float], alpha: float = 0.05) -> list[HolmResult]:
    """Step-down Holm-Bonferroni correction, returned in input order.

    Drift detection scans every threshold, so without a correction the module
    would manufacture findings at the usual rate. Rejections stop at the first
    non-rejection, which is what makes the procedure step-down.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be strictly between 0 and 1, got {alpha}")
    for value in p_values:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"p-values must be within [0, 1], got {value}")
    if not p_values:
        return []

    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    results: list[HolmResult] = []
    still_rejecting = True
    for rank, index in enumerate(order):
        adjusted_alpha = alpha / (m - rank)
        rejected = still_rejecting and p_values[index] <= adjusted_alpha
        still_rejecting = rejected
        results.append(
            HolmResult(
                index=index,
                p_value=p_values[index],
                adjusted_alpha=adjusted_alpha,
                rejected=rejected,
            )
        )
    return sorted(results, key=lambda r: r.index)


def brier_score(predictions: Iterable[tuple[float, bool]]) -> float:
    """Mean squared error of probabilistic predictions (lower is better)."""
    pairs = list(predictions)
    if not pairs:
        raise ValueError("brier_score needs at least one prediction")
    total = 0.0
    for probability, outcome in pairs:
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"predicted probabilities must be within [0, 1], got {probability}")
        total += (probability - (1.0 if outcome else 0.0)) ** 2
    return total / len(pairs)


def too_few_samples(n: int, *, min_samples: int = DEFAULT_MIN_SAMPLES) -> bool:
    """Whether a sample is too small for its rate to be reported as a finding."""
    return n < min_samples


def sample_warning(
    successes: int,
    n: int,
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_events: int = DEFAULT_MIN_EVENTS,
) -> str | None:
    """A human-readable caveat, or ``None`` when the sample supports a claim."""
    _validate_counts(successes, n)
    if n < min_samples:
        return (
            f"only {n} sample(s); at least {min_samples} are needed before this rate means "
            "anything - treat it as provisional and widen the interval before acting"
        )
    if successes < min_events or (n - successes) < min_events:
        return (
            f"only {successes} of {n} samples observed an event; the interval's upper bound is "
            "the meaningful number here, not the point estimate"
        )
    return None
