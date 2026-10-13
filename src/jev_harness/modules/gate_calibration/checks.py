"""The five gate_calibration checks. All deterministic; none needs a model."""

from __future__ import annotations

from dataclasses import dataclass, field

from jev_harness.core.check import Check, Finding, Severity
from jev_harness.core.stats import sample_warning, too_few_samples
from jev_harness.modules.gate_calibration.data import GateRow
from jev_harness.modules.gate_calibration.metrics import (
    DriftFinding,
    Recommendation,
    ReliabilityBin,
    ThresholdRow,
)

MODULE = "calibrate"
DEFAULT_ECE_TARGET = 0.05
DEFAULT_MISS_TARGET = 0.01
MIN_BIN_SAMPLES = 10


@dataclass(slots=True)
class Analysis:
    """Everything the checks need, computed once."""

    rows: list[GateRow]
    skip_decision: str
    labeled: int
    bins: list[ReliabilityBin]
    ece: float | None
    brier: float | None
    table: list[ThresholdRow]
    recommendation: Recommendation
    miss_target: float
    min_samples: int
    chosen: ThresholdRow | None = None
    weighted: bool = False
    notes: list[str] = field(default_factory=list)


class EceAboveTarget(Check):
    """Stated confidence disagrees with observed accuracy overall."""

    rule_id = f"{MODULE}.ece_above_target"
    severity = Severity.flag

    def __init__(self, target: float = DEFAULT_ECE_TARGET) -> None:
        self.target = target

    def run(self, subject: Analysis) -> list[Finding]:
        if subject.ece is None or subject.ece <= self.target:
            return []
        small = too_few_samples(subject.labeled, min_samples=subject.min_samples)
        return [
            self.finding(
                f"expected calibration error is {subject.ece:.3f} over {subject.labeled} labelled "
                f"decisions, above the {self.target:.3f} target"
                + ("; the sample is too small to be conclusive" if small else ""),
                severity=Severity.info if small else Severity.flag,
                location="overall",
                evidence={
                    "ece": subject.ece,
                    "target": self.target,
                    "labeled": subject.labeled,
                    "brier": subject.brier,
                },
            )
        ]


class OverconfidentBucket(Check):
    """A confidence range where the gate is reliably less accurate than it claims."""

    rule_id = f"{MODULE}.overconfident_bucket"
    severity = Severity.flag

    def __init__(self, min_bin_samples: int = MIN_BIN_SAMPLES) -> None:
        self.min_bin_samples = min_bin_samples

    def run(self, subject: Analysis) -> list[Finding]:
        findings: list[Finding] = []
        for entry in subject.bins:
            if entry.n < self.min_bin_samples or entry.interval.high >= entry.mean_confidence:
                continue
            findings.append(
                self.finding(
                    f"confidence {entry.low:.2f}-{entry.high:.2f}: the gate says "
                    f"{entry.mean_confidence:.0%} but was right {entry.accuracy:.0%} of the time "
                    f"({entry.interval})",
                    location=f"bin:{entry.low:.2f}-{entry.high:.2f}",
                    evidence={
                        "n": entry.n,
                        "mean_confidence": entry.mean_confidence,
                        "accuracy": entry.accuracy,
                        "ci_low": entry.interval.low,
                        "ci_high": entry.interval.high,
                        "weighted": entry.weighted,
                    },
                )
            )
        return findings


class MissBoundBreach(Check):
    """Misses at the chosen threshold (or at every threshold) are above the target."""

    rule_id = f"{MODULE}.miss_bound_breach"
    severity = Severity.escalate

    def run(self, subject: Analysis) -> list[Finding]:
        if subject.chosen is not None:
            row = subject.chosen
            if row.skipped_labeled == 0 or row.miss_rate <= subject.miss_target:
                return []
            return [
                self.finding(
                    f"at threshold {row.threshold:g} the gate let through {row.misses.successes} "
                    f"of {row.misses.n} labelled skips ({row.miss_rate:.1%}), above the "
                    f"{subject.miss_target:.1%} target",
                    location=f"threshold:{row.threshold:g}",
                    evidence=_row_evidence(row, subject.miss_target),
                )
            ]
        nearest = subject.recommendation.nearest
        if (
            subject.recommendation.threshold is None
            and nearest is not None
            and nearest.miss_rate >= subject.miss_target
        ):
            return [
                self.finding(
                    f"no threshold keeps misses under {subject.miss_target:.1%}: the best "
                    f"observed miss rate is {nearest.miss_rate:.1%} at threshold "
                    f"{nearest.threshold:g}",
                    location="all-thresholds",
                    evidence=_row_evidence(nearest, subject.miss_target),
                )
            ]
        return []


class InsufficientLabels(Check):
    """There is not enough labelled data to support a threshold decision."""

    rule_id = f"{MODULE}.insufficient_labels"
    severity = Severity.flag

    def run(self, subject: Analysis) -> list[Finding]:
        findings: list[Finding] = []
        if too_few_samples(subject.labeled, min_samples=subject.min_samples):
            findings.append(
                self.finding(
                    f"only {subject.labeled} labelled decision(s); at least {subject.min_samples} "
                    "are needed before calibration numbers mean anything",
                    location="labels",
                    evidence={"labeled": subject.labeled, "min_samples": subject.min_samples},
                )
            )
        recommendation = subject.recommendation
        if recommendation.additional_labels is not None and recommendation.nearest is not None:
            nearest = recommendation.nearest
            findings.append(
                self.finding(
                    f"the data cannot yet show a miss rate under {subject.miss_target:.1%}: at "
                    f"threshold {nearest.threshold:g} the upper bound is "
                    f"{nearest.upper_bound:.1%} from {nearest.skipped_labeled} labelled skips; "
                    f"about {recommendation.additional_labels} more labelled skips at the same "
                    "observed miss rate would settle it",
                    location=f"threshold:{nearest.threshold:g}",
                    evidence={
                        **_row_evidence(nearest, subject.miss_target),
                        "additional_labels": recommendation.additional_labels,
                    },
                )
            )
        if recommendation.threshold is None and recommendation.nearest is None:
            findings.append(
                self.finding(
                    recommendation.reason.capitalize(),
                    location="labels",
                    evidence={"labeled": subject.labeled},
                )
            )
        warning = sample_warning(
            sum(1 for r in subject.rows if r.labeled and r.correct),
            subject.labeled,
            min_samples=subject.min_samples,
        )
        if warning and not findings:
            findings.append(
                self.finding(warning, severity=Severity.info, location="labels", evidence={})
            )
        return findings


class DriftDetected(Check):
    """Per-bin accuracy changed between two windows (Holm-corrected)."""

    rule_id = f"{MODULE}.drift_detected"
    severity = Severity.flag

    def run(self, subject: list[DriftFinding]) -> list[Finding]:
        return [
            self.finding(
                f"{item.name}: accuracy moved from {item.a.estimate:.0%} to {item.b.estimate:.0%} "
                f"(p={item.p_value:.4f}, Holm-adjusted alpha {item.adjusted_alpha:.4f})",
                location=item.name,
                evidence={
                    "window_a": {"accuracy": item.a.estimate, "n": item.a.n},
                    "window_b": {"accuracy": item.b.estimate, "n": item.b.n},
                    "p_value": item.p_value,
                    "adjusted_alpha": item.adjusted_alpha,
                },
            )
            for item in subject
            if item.rejected
        ]


def _row_evidence(row: ThresholdRow, target: float) -> dict[str, object]:
    return {
        "threshold": row.threshold,
        "skipped_share": row.skipped_share,
        "skipped_labeled": row.skipped_labeled,
        "misses": row.misses.successes,
        "miss_rate": row.miss_rate,
        "upper_bound": row.upper_bound,
        "bound_method": row.bound_method,
        "target": target,
        "weighted": row.misses.weighted,
        "risky_escape_rate": row.risky_escape_rate,
    }


def default_checks(ece_target: float = DEFAULT_ECE_TARGET) -> list[Check]:
    return [
        EceAboveTarget(ece_target),
        OverconfidentBucket(),
        MissBoundBreach(),
        InsufficientLabels(),
    ]
