"""Compose loading, metrics, checks, and reports for ``calibrate``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jev_harness.core.check import Eval, Finding
from jev_harness.core.errors import ConfigError
from jev_harness.core.report import Report
from jev_harness.core.stats import Interval
from jev_harness.modules.gate_calibration import checks, metrics
from jev_harness.modules.gate_calibration.data import (
    GateRow,
    load_log,
    merge_labels,
    sample_sheet,
    write_sheet,
)


class CalibrateEval(Eval):
    module = checks.MODULE


@dataclass(slots=True)
class LoadedLog:
    rows: list[GateRow]
    warnings: list[str]


def load(log: Path, labels: Path | None = None) -> LoadedLog:
    rows = load_log(log)
    warnings: list[str] = []
    if labels is not None:
        merged = merge_labels(rows, labels)
        rows = merged.rows
        if merged.unmatched_ids:
            shown = ", ".join(merged.unmatched_ids[:5])
            warnings.append(
                f"{len(merged.unmatched_ids)} label(s) matched no log row and were ignored "
                f"(first: {shown})"
            )
        if merged.blank_labels:
            warnings.append(
                f"{merged.blank_labels} sheet row(s) have no label yet and are unscored"
            )
    return LoadedLog(rows, warnings)


def make_sheet(log: Path, out: Path, n: int, *, bins: int = 5, seed: int = 0) -> int:
    return write_sheet(sample_sheet(load_log(log), n, bins=bins, seed=seed), out)


def summarize_inputs(loaded: LoadedLog, skip_decision: str) -> list[str]:
    rows = loaded.rows
    decisions = sorted({r.decision for r in rows})
    labeled = sum(1 for r in rows if r.labeled)
    lines = [
        f"rows:            {len(rows)}",
        f"labelled:        {labeled}",
        f"unlabelled:      {len(rows) - labeled} (counted, not scored)",
        f"decisions:       {', '.join(decisions)}",
        f"skip decision:   {skip_decision}"
        + ("" if skip_decision in decisions else "  (WARNING: no row has this decision)"),
        f"weighted rows:   {sum(1 for r in rows if r.weight != 1.0)}",
    ]
    lines.extend(f"warning: {w}" for w in loaded.warnings)
    lines.append("dry run: nothing was scored.")
    return lines


def analyse(
    rows: list[GateRow],
    *,
    skip_decision: str,
    miss_target: float,
    bins: int,
    min_samples: int,
    threshold: float | None,
) -> checks.Analysis:
    labelled = [r for r in rows if r.labeled]
    table = metrics.threshold_table(rows, skip_decision)
    chosen = (
        metrics.evaluate_threshold(rows, skip_decision, threshold)
        if threshold is not None
        else None
    )
    return checks.Analysis(
        rows=rows,
        skip_decision=skip_decision,
        labeled=len(labelled),
        bins=metrics.reliability_table(labelled, bins),
        ece=metrics.expected_calibration_error(labelled, bins) if labelled else None,
        brier=metrics.weighted_brier(labelled) if labelled else None,
        table=table,
        recommendation=metrics.recommend(table, miss_target),
        miss_target=miss_target,
        min_samples=min_samples,
        chosen=chosen,
        weighted=any(r.weight != 1.0 for r in labelled),
    )


def _bin_line(entry: metrics.ReliabilityBin) -> str:
    return (
        f"n={entry.n}; says {entry.mean_confidence:.0%}, right {entry.accuracy:.0%} "
        f"(95% CI {entry.interval.low:.0%} to {entry.interval.high:.0%})"
    )


def build_report(
    loaded: LoadedLog,
    *,
    skip_decision: str = "skip",
    miss_target: float = checks.DEFAULT_MISS_TARGET,
    ece_target: float = checks.DEFAULT_ECE_TARGET,
    bins: int = 10,
    min_samples: int = 30,
    threshold: float | None = None,
) -> Report:
    if not 0.0 < miss_target < 1.0:
        raise ConfigError("--target must be strictly between 0 and 1")
    if bins < 2:
        raise ConfigError("--bins must be at least 2")
    rows = loaded.rows
    analysis = analyse(
        rows,
        skip_decision=skip_decision,
        miss_target=miss_target,
        bins=bins,
        min_samples=min_samples,
        threshold=threshold,
    )
    evaluator = CalibrateEval(checks.default_checks(ece_target))
    findings: list[Finding] = evaluator.run_checks(analysis)

    metric_rows: dict[str, Any] = {
        "rows": len(rows),
        "labeled": analysis.labeled,
        "unlabeled": len(rows) - analysis.labeled,
        "skip_decision": skip_decision,
        "ece": None if analysis.ece is None else round(analysis.ece, 4),
        "brier": None if analysis.brier is None else round(analysis.brier, 4),
        "miss_target": miss_target,
    }
    for entry in analysis.bins:
        metric_rows[f"reliability.{entry.low:.2f}-{entry.high:.2f}"] = _bin_line(entry)

    recommendation = analysis.recommendation
    if recommendation.row is not None:
        row = recommendation.row
        metric_rows["recommended_threshold"] = row.threshold
        metric_rows["recommended.skipped_share"] = f"{row.skipped_share:.1%} of all decisions"
        metric_rows["recommended.misses"] = (
            f"{row.misses.successes}/{row.misses.n} labelled skips; upper bound "
            f"{row.upper_bound:.2%} ({row.bound_method})"
        )
    else:
        metric_rows["recommended_threshold"] = "none: " + recommendation.reason
    if analysis.chosen is not None:
        c = analysis.chosen
        metric_rows[f"threshold.{c.threshold:g}"] = (
            f"skips {c.skipped_share:.1%}; {c.misses.successes}/{c.misses.n} misses "
            f"({c.miss_rate:.2%}); upper bound {c.upper_bound:.2%} ({c.bound_method})"
        )

    limitations = [
        "Correct means the gate's decision equals the label. A miss is a skip decision whose "
        "label says the expensive step was needed; the miss rate is over labelled skips, and "
        "`risky_escape_rate` in each finding's evidence is over all risky items.",
        "The upper bound assumes labelled rows are a fair sample of what the gate will see. "
        "Labels you chose because they looked interesting will bias it.",
        *loaded.warnings,
    ]
    if analysis.weighted:
        limitations.append(
            "Labels came from a stratified sheet, so rows are weighted by inverse sampling "
            "probability and intervals use Kish's effective sample size. That is an "
            "approximation."
        )
    if any(r.bound_method == "wilson" for r in analysis.table):
        limitations.append(
            "scipy is not installed, so miss-rate upper bounds use the Wilson interval instead "
            "of the exact Clopper-Pearson bound; install `jev-harness[stats]` for the exact bound."
        )
    if analysis.labeled < len(rows):
        limitations.append(
            f"{len(rows) - analysis.labeled} of {len(rows)} rows have no label and are not scored."
        )

    summary = f"{analysis.labeled} of {len(rows)} decisions are labelled. " + (
        f"Recommended threshold: {recommendation.threshold:g}."
        if recommendation.threshold is not None
        else "No threshold can be recommended yet."
    )
    return evaluator.build_report(
        title="calibrate: gate confidence vs observed accuracy",
        summary=summary,
        findings=findings,
        metrics=metric_rows,
        meta={"skip_decision": skip_decision, "bins": bins, "weighted": analysis.weighted},
        limitations=limitations,
    ).model_copy(
        update={
            "headline": [
                "labeled",
                "ece",
                "brier",
                "recommended_threshold",
                "recommended.*",
                "threshold.*",
                "reliability.*",
            ]
        }
    )


def _window(rows: list[GateRow], start: datetime | None, end: datetime | None) -> list[GateRow]:
    return [
        r
        for r in rows
        if r.timestamp is not None
        and (start is None or r.timestamp >= start)
        and (end is None or r.timestamp < end)
    ]


def build_drift_report(
    window_a: list[GateRow],
    window_b: list[GateRow],
    *,
    bins: int = 5,
    min_per_window: int = 10,
    description: str = "",
) -> Report:
    scan, skipped = metrics.drift_scan(window_a, window_b, bins=bins, min_per_window=min_per_window)
    check = checks.DriftDetected()
    evaluator = CalibrateEval([check])
    findings = check.run(scan)
    metric_rows: dict[str, Any] = {
        "window_a_rows": len(window_a),
        "window_b_rows": len(window_b),
        "window_a_labeled": sum(1 for r in window_a if r.labeled),
        "window_b_labeled": sum(1 for r in window_b if r.labeled),
        "bins_tested": len(scan),
    }
    for item in scan:
        a: Interval = item.a
        b: Interval = item.b
        metric_rows[f"accuracy.{item.name}"] = (
            f"{a.estimate:.0%} (n={a.n}) -> {b.estimate:.0%} (n={b.n}); p={item.p_value:.4f}"
        )
    limitations = [
        "Each confidence bin is a separate hypothesis; p-values are Holm-Bonferroni corrected "
        "across bins so a scan does not manufacture drift.",
        "Drift is reported only for bins with enough labelled decisions in both windows.",
    ]
    if skipped:
        limitations.append(f"Bins skipped for too few labelled rows: {', '.join(skipped)}")
    if not scan:
        limitations.append(
            "No bin had enough labelled rows in both windows, so nothing was tested."
        )
    summary = (
        f"{len(findings)} of {len(scan)} tested confidence bin(s) show a change in accuracy"
        + (f" ({description})" if description else "")
        + "."
    )
    return evaluator.build_report(
        title="calibrate: drift between two windows",
        summary=summary,
        findings=sorted(findings, key=lambda f: f.location or ""),
        metrics=metric_rows,
        meta={"bins": bins, "min_per_window": min_per_window},
        limitations=limitations,
    ).model_copy(
        update={"headline": ["window_a_labeled", "window_b_labeled", "bins_tested", "accuracy.*"]}
    )


def split_windows(
    rows: list[GateRow], split: datetime | None, other: list[GateRow] | None
) -> tuple[list[GateRow], list[GateRow], str]:
    if other is not None:
        return rows, other, "log vs --against"
    if split is None:
        raise ConfigError("drift needs --split TIMESTAMP or --against OTHER_LOG")
    if split.tzinfo is None:
        raise ConfigError("--split must include a timezone, for example 2026-09-01T00:00:00Z")
    if not any(r.timestamp for r in rows):
        raise ConfigError("the log has no timestamp field, so it cannot be split into windows")
    return (
        _window(rows, None, split),
        _window(rows, split, None),
        f"before vs from {split.isoformat()}",
    )


__all__ = [
    "LoadedLog",
    "analyse",
    "build_drift_report",
    "build_report",
    "load",
    "make_sheet",
    "split_windows",
    "summarize_inputs",
]
