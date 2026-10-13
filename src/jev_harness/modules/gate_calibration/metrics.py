"""Calibration metrics for a gate, all computed from labelled rows.

Terms used throughout:

* a row is *correct* when the gate's decision equals the label;
* a *skip decision* is one whose decision equals the configured skip value, meaning the gate says
  the expensive step can be skipped; and
* a *miss* is a skip decision whose label says the expensive step was needed.

The miss rate is therefore "of the items the gate let through at this threshold, how many should
not have been", which is the number a threshold has to be chosen against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from jev_harness.core.stats import (
    Interval,
    MissingStatsExtraError,
    holm_bonferroni,
    two_proportion_p_value,
    upper_bound,
    wilson,
)
from jev_harness.modules.gate_calibration.data import GateRow, bin_index

MAX_THRESHOLDS = 200
MAX_LABELS_SEARCH = 1_000_000


@dataclass(frozen=True, slots=True)
class WeightedCount:
    """A proportion from possibly weighted rows, reduced to integer counts for intervals."""

    successes: int
    n: int
    estimate: float
    weighted: bool

    @property
    def interval(self) -> Interval:
        return wilson(self.successes, self.n) if self.n else wilson(0, 0)


def weighted_count(rows: list[GateRow], event: list[bool]) -> WeightedCount:
    """Count ``event`` over ``rows``. Unweighted rows give exact counts.

    With weights (from a stratified sheet) the estimate is the weighted proportion and the
    interval uses Kish's effective sample size, rounded conservatively. That is an approximation,
    and callers label it as one.
    """
    if not rows:
        return WeightedCount(0, 0, 0.0, False)
    if all(row.weight == 1.0 for row in rows):
        k = sum(event)
        return WeightedCount(k, len(rows), k / len(rows), False)
    total = sum(row.weight for row in rows)
    estimate = sum(row.weight for row, hit in zip(rows, event, strict=True) if hit) / total
    n_eff = total**2 / sum(row.weight**2 for row in rows)
    n = max(1, math.floor(n_eff))
    k = min(n, math.ceil(estimate * n))
    return WeightedCount(k, n, estimate, True)


def miss_upper_bound(count: WeightedCount, confidence: float = 0.95) -> tuple[float, str]:
    """Upper confidence bound on a miss rate: exact when scipy is available, else Wilson."""
    if count.n == 0:
        return 1.0, "none"
    try:
        return upper_bound(count.successes, count.n, confidence, exact=True), "clopper-pearson"
    except MissingStatsExtraError:
        return upper_bound(count.successes, count.n, confidence), "wilson"


# ------------------------------------------------------------------ reliability


@dataclass(frozen=True, slots=True)
class ReliabilityBin:
    index: int
    low: float
    high: float
    n: int
    mean_confidence: float
    accuracy: float
    interval: Interval
    weighted: bool

    @property
    def gap(self) -> float:
        """Confidence minus accuracy: positive means the gate is overconfident here."""
        return self.mean_confidence - self.accuracy


def reliability_table(rows: list[GateRow], bins: int = 10) -> list[ReliabilityBin]:
    """Per-confidence-bin accuracy over labelled rows; empty bins are omitted."""
    grouped: dict[int, list[GateRow]] = {}
    for row in rows:
        if row.labeled:
            grouped.setdefault(bin_index(row.confidence, bins), []).append(row)
    table: list[ReliabilityBin] = []
    for index in sorted(grouped):
        members = grouped[index]
        count = weighted_count(members, [row.correct for row in members])
        total_weight = sum(row.weight for row in members)
        mean_conf = sum(row.confidence * row.weight for row in members) / total_weight
        table.append(
            ReliabilityBin(
                index=index,
                low=index / bins,
                high=(index + 1) / bins,
                n=len(members),
                mean_confidence=mean_conf,
                accuracy=count.estimate,
                interval=count.interval,
                weighted=count.weighted,
            )
        )
    return table


def expected_calibration_error(rows: list[GateRow], bins: int = 10) -> float:
    """Weight-averaged absolute gap between confidence and accuracy across bins."""
    labelled = [row for row in rows if row.labeled]
    if not labelled:
        raise ValueError("expected_calibration_error needs at least one labelled row")
    total = sum(row.weight for row in labelled)
    ece = 0.0
    for entry in reliability_table(labelled, bins):
        members = [r for r in labelled if bin_index(r.confidence, bins) == entry.index]
        ece += sum(r.weight for r in members) / total * abs(entry.gap)
    return ece


def weighted_brier(rows: list[GateRow]) -> float:
    labelled = [row for row in rows if row.labeled]
    if not labelled:
        raise ValueError("weighted_brier needs at least one labelled row")
    total = sum(row.weight for row in labelled)
    return (
        sum(row.weight * (row.confidence - (1.0 if row.correct else 0.0)) ** 2 for row in labelled)
        / total
    )


# ------------------------------------------------------------------- thresholds


@dataclass(frozen=True, slots=True)
class ThresholdRow:
    threshold: float
    skipped_share: float
    skipped_labeled: int
    misses: WeightedCount
    miss_rate: float
    upper_bound: float
    bound_method: str
    risky_escape_rate: float | None


def candidate_thresholds(rows: list[GateRow], skip_decision: str) -> list[float]:
    values = sorted({r.confidence for r in rows if r.decision == skip_decision})
    if len(values) <= MAX_THRESHOLDS:
        return values
    step = (len(values) - 1) / (MAX_THRESHOLDS - 1)
    return sorted({values[round(i * step)] for i in range(MAX_THRESHOLDS)})


def evaluate_threshold(rows: list[GateRow], skip_decision: str, threshold: float) -> ThresholdRow:
    """Skip when the gate says ``skip_decision`` with confidence at least ``threshold``."""
    skipped_all = [r for r in rows if r.decision == skip_decision and r.confidence >= threshold]
    skipped = [r for r in skipped_all if r.labeled]
    misses = weighted_count(skipped, [r.label != skip_decision for r in skipped])
    bound, method = miss_upper_bound(misses)

    labelled = [r for r in rows if r.labeled]
    risky = [r for r in labelled if r.label != skip_decision]
    risky_total = sum(r.weight for r in risky)
    escaped = sum(r.weight for r in skipped if r.label != skip_decision)
    return ThresholdRow(
        threshold=threshold,
        skipped_share=len(skipped_all) / len(rows) if rows else 0.0,
        skipped_labeled=len(skipped),
        misses=misses,
        miss_rate=misses.estimate,
        upper_bound=bound,
        bound_method=method,
        risky_escape_rate=escaped / risky_total if risky_total else None,
    )


def threshold_table(rows: list[GateRow], skip_decision: str) -> list[ThresholdRow]:
    return [
        evaluate_threshold(rows, skip_decision, t)
        for t in candidate_thresholds(rows, skip_decision)
    ]


def labels_needed(observed_rate: float, target: float, confidence: float = 0.95) -> int | None:
    """Skipped, labelled items needed for the upper bound to reach ``target`` at ``observed_rate``.

    Returns ``None`` when the observed rate is already at or above the target: more labels cannot
    fix a gate that is not good enough. The search keeps the observed rate fixed as n grows.
    """
    if observed_rate >= target:
        return None
    low, high = 1, 1
    while high < MAX_LABELS_SEARCH and not _bound_ok(observed_rate, high, target, confidence):
        low, high = high, high * 2
    if not _bound_ok(observed_rate, high, target, confidence):
        return None
    while low < high:
        middle = (low + high) // 2
        if _bound_ok(observed_rate, middle, target, confidence):
            high = middle
        else:
            low = middle + 1
    return high


def _bound_ok(rate: float, n: int, target: float, confidence: float) -> bool:
    k = min(n, round(rate * n))
    try:
        bound = upper_bound(k, n, confidence, exact=True)
    except MissingStatsExtraError:
        bound = upper_bound(k, n, confidence)
    return bound <= target


@dataclass(frozen=True, slots=True)
class Recommendation:
    threshold: float | None
    row: ThresholdRow | None
    reason: str
    #: Closest candidate when none qualifies, and how many more skipped labels it would need.
    nearest: ThresholdRow | None = None
    additional_labels: int | None = None
    notes: list[str] = field(default_factory=list)


def recommend(table: list[ThresholdRow], target: float) -> Recommendation:
    """Lowest-cost threshold whose upper-bound miss rate is within ``target``.

    Cost is the expensive step you still pay for, so the cheapest qualifying threshold is the one
    that skips the most. If none qualifies, the nearest candidate is the one with the lowest upper
    bound among those with enough labelled skips to say anything, and the result says whether more
    labels could help or the gate is simply not good enough.
    """
    usable = [row for row in table if row.skipped_labeled > 0]
    if not usable:
        return Recommendation(None, None, "no labelled skip decisions to evaluate")
    qualifying = [row for row in usable if row.upper_bound <= target]
    if qualifying:
        best = max(qualifying, key=lambda r: (r.skipped_share, -r.threshold))
        return Recommendation(best.threshold, best, "meets the target with the stated confidence")

    nearest = min(usable, key=lambda r: (r.upper_bound, -r.skipped_share))
    needed = labels_needed(nearest.miss_rate, target)
    if needed is None:
        return Recommendation(
            None,
            None,
            "no threshold keeps misses under the target: even the best observed miss rate is "
            "at or above it, so more labels will not help",
            nearest=nearest,
        )
    extra = max(0, needed - nearest.skipped_labeled)
    return Recommendation(
        None,
        None,
        "the data cannot yet show that any threshold meets the target",
        nearest=nearest,
        additional_labels=extra,
    )


# ------------------------------------------------------------------------ drift


@dataclass(frozen=True, slots=True)
class DriftFinding:
    name: str
    a: Interval
    b: Interval
    p_value: float
    adjusted_alpha: float
    rejected: bool


def drift_scan(
    window_a: list[GateRow],
    window_b: list[GateRow],
    *,
    bins: int = 5,
    min_per_window: int = 10,
    alpha: float = 0.05,
) -> tuple[list[DriftFinding], list[str]]:
    """Compare per-bin accuracy between two windows, with a Holm correction across bins.

    Returns every tested hypothesis (rejected or not) and the bins skipped for being too small.
    """
    tested: list[tuple[str, int, int, int, int]] = []
    skipped: list[str] = []
    for index in range(bins):
        rows_a = [r for r in window_a if r.labeled and bin_index(r.confidence, bins) == index]
        rows_b = [r for r in window_b if r.labeled and bin_index(r.confidence, bins) == index]
        name = f"confidence {index / bins:.2f}-{(index + 1) / bins:.2f}"
        if len(rows_a) < min_per_window or len(rows_b) < min_per_window:
            if rows_a or rows_b:
                skipped.append(f"{name} (n={len(rows_a)} vs {len(rows_b)})")
            continue
        tested.append(
            (
                name,
                sum(r.correct for r in rows_a),
                len(rows_a),
                sum(r.correct for r in rows_b),
                len(rows_b),
            )
        )
    p_values = [two_proportion_p_value(ka, na, kb, nb) for _, ka, na, kb, nb in tested]
    corrected = holm_bonferroni(p_values, alpha)
    findings = [
        DriftFinding(
            name=name,
            a=wilson(ka, na),
            b=wilson(kb, nb),
            p_value=corrected[i].p_value,
            adjusted_alpha=corrected[i].adjusted_alpha,
            rejected=corrected[i].rejected,
        )
        for i, (name, ka, na, kb, nb) in enumerate(tested)
    ]
    return findings, skipped
