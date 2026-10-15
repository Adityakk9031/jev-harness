"""Report rendering: JSON, Markdown, and GitHub step summary.

One :class:`Report` per module run, three renderings of it. Two constraints
shape this module:

* **Markdown must be byte-stable** so it can be snapshot-tested and diffed
  between runs. Findings are sorted, dictionary keys are sorted, and the
  generation timestamp appears only in JSON.
* **Nothing misleading is allowed to render quietly.** A rate without a sample
  size, or an emulated matcher presented as authoritative, is a bug, so the
  renderer surfaces ``limitations`` and a banner for approximated results.

GitHub annotations (``::error::`` / ``::warning::``) are produced separately
from the step summary by :func:`workflow_annotations`, since mixing workflow
commands into a summary file does not work. (The function is not called
``annotations`` because ``from __future__ import annotations`` binds that very
name in every module in this package.)
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.check import Finding, Severity, sort_findings
from jev_harness.core.config import FailOn


class OutputFormat(StrEnum):
    """Rendering targets."""

    json = "json"
    md = "md"
    github = "github"
    #: A short terminal view: the verdict, the headline numbers, and the top findings.
    summary = "summary"

    @classmethod
    def parse(cls, value: str) -> OutputFormat:
        """Accept user-facing aliases such as ``markdown``."""
        normalized = value.strip().lower()
        aliases = {"markdown": cls.md, "step-summary": cls.github, "gh": cls.github}
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError as exc:
            allowed = ", ".join(member.value for member in cls)
            raise ValueError(f"unknown format {value!r}; expected one of: {allowed}") from exc


class Report(BaseModel):
    """The single result type every module returns."""

    model_config = ConfigDict(extra="forbid")

    module: str
    title: str
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    #: Metric keys (or ``prefix.*`` patterns) the short summary shows first, in this order.
    headline: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def ordered_findings(self) -> list[Finding]:
        return sort_findings(self.findings)

    def severity_counts(self) -> dict[Severity, int]:
        counts = dict.fromkeys(Severity, 0)
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts

    def by_severity(self, severity: Severity) -> list[Finding]:
        return [finding for finding in self.ordered_findings() if finding.severity is severity]

    def should_fail(self, fail_on: FailOn) -> bool:
        """Whether this report meets the configured fail-on condition."""
        counts = self.severity_counts()
        if fail_on is FailOn.escalate:
            return counts[Severity.escalate] > 0
        return counts[Severity.escalate] > 0 or counts[Severity.flag] > 0

    def render(self, fmt: OutputFormat | str) -> str:
        """Render this report in the requested format."""
        target = fmt if isinstance(fmt, OutputFormat) else OutputFormat.parse(fmt)
        if target is OutputFormat.json:
            return self.model_dump_json(indent=2)
        if target is OutputFormat.summary:
            return _render_summary(self)
        return _render_markdown(self)

    def write(self, fmt: OutputFormat | str, path: Path) -> str:
        """Render and write, creating parent directories. Returns the text."""
        text = self.render(fmt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text


# --------------------------------------------------------------------- helpers


def _cell(value: Any) -> str:
    """Escape a value for a Markdown table cell.

    Guard payloads and reviewer comments routinely contain pipes and newlines;
    unescaped, they would silently break every table in the report.
    """
    text = _format_value(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def _confidence_cell(finding: Finding) -> str:
    return "" if finding.confidence is None else f"{finding.confidence:.2f}"


def _severity_line(counts: dict[Severity, int]) -> str:
    order = (Severity.escalate, Severity.flag, Severity.info)
    if not any(counts.values()):
        return "no findings"
    return " · ".join(f"{counts[severity]} {severity.value}" for severity in order)


def _banner_lines(report: Report) -> list[str]:
    """Warnings that must appear above the findings table."""
    lines: list[str] = []
    if report.meta.get("matcher") == "emulated":
        lines.append(
            "**Approximation.** Permission matching in this report is emulated, not the agent's "
            "own evaluator. Treat allow/deny results for permission rules as indicative only; "
            "hook results are real."
        )
    if report.meta.get("mode") == "simulated":
        lines.append(
            "**Simulated run.** Compaction was simulated with a local summarizer, so these "
            "survival rates measure the harness pipeline, not the agent under test."
        )
    return lines


def _finding_row(finding: Finding) -> str:
    """One findings-table row, with every cell escaped."""
    return (
        f"| `{_cell(finding.rule_id)}`"
        f" | {_cell(finding.severity.value)}"
        f" | {_cell(finding.message)}"
        f" | {_cell(finding.location)}"
        f" | {_cell(finding.source.value)}"
        f" | {_cell(_confidence_cell(finding))} |"
    )


def _render_markdown(report: Report) -> str:
    counts = report.severity_counts()
    lines: list[str] = [f"# {report.title}", "", report.summary, ""]

    meta_rows = {
        "Module": report.module,
        "Findings": _severity_line(counts),
        **{f"meta.{key}": value for key, value in sorted(report.meta.items())},
    }
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    for key, value in meta_rows.items():
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.append("")

    for banner in _banner_lines(report):
        lines.append(banner)
        lines.append("")

    lines.append("## Metrics")
    lines.append("")
    if report.metrics:
        rows = [
            f"| {_cell(key)} | {_cell(value)} |" for key, value in sorted(report.metrics.items())
        ]
        lines.extend(["| Metric | Value |", "| --- | --- |", *rows])
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines.append("## Findings")
    lines.append("")
    ordered = report.ordered_findings()
    if not ordered:
        lines.append("_None._")
        lines.append("")
    else:
        lines.extend(
            [
                "| Rule | Severity | Message | Location | Source | Confidence |",
                "| --- | --- | --- | --- | --- | --- |",
                *[_finding_row(finding) for finding in ordered],
            ]
        )
        lines.append("")

        evidential = [f for f in ordered if f.evidence]
        if evidential:
            lines.append("### Evidence")
            lines.append("")
            for finding in evidential:
                header = f"**`{finding.rule_id}`**"
                if finding.location:
                    header = f"{header} ({finding.location})"
                lines.append(header)
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(finding.evidence, indent=2, sort_keys=True, default=str))
                lines.append("```")
                lines.append("")

    lines.append("## Limitations")
    lines.append("")
    if report.limitations:
        lines.extend(f"- {limitation}" for limitation in report.limitations)
    else:
        lines.append("_None recorded. Absence here is not a claim of validity._")
    lines.append("")
    return "\n".join(lines)


SUMMARY_MAX_FINDINGS = 6
SUMMARY_MAX_METRICS = 18
SUMMARY_MAX_EXAMPLES = 2
SUMMARY_WIDTH = 110


def _clip(text: str, width: int = SUMMARY_WIDTH) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _headline_metrics(report: Report) -> list[tuple[str, Any]]:
    """The metrics a reader needs first: the module's headline list, else the undotted keys."""
    chosen: list[tuple[str, Any]] = []
    seen: set[str] = set()
    patterns = report.headline or [k for k in report.metrics if "." not in k]
    for pattern in patterns:
        if pattern.endswith(".*"):
            prefix = pattern[:-1]
            keys = [k for k in report.metrics if k.startswith(prefix)]
        else:
            keys = [pattern] if pattern in report.metrics else []
        for key in keys:
            if key not in seen:
                seen.add(key)
                chosen.append((key, report.metrics[key]))
    return chosen


def _render_summary(report: Report) -> str:
    """A short, plain-text view for a terminal. Nothing is hidden: it points at the full report."""
    counts = report.severity_counts()
    lines = [report.title, _clip(report.summary, 200), ""]
    metrics = _headline_metrics(report)
    if metrics:
        lines.append("Key numbers")
        width = min(48, max(len(k) for k, _ in metrics[:SUMMARY_MAX_METRICS]))
        # Values are never cut: a truncated number ("1 unc…") hides exactly what a reader needs.
        lines.extend(
            f"  {key:<{width}}  {' '.join(_format_value(value).split())}"
            for key, value in metrics[:SUMMARY_MAX_METRICS]
        )
        if len(metrics) > SUMMARY_MAX_METRICS:
            lines.append(f"  … {len(metrics) - SUMMARY_MAX_METRICS} more in the full report")
        lines.append("")
    lines.append(f"Findings: {_severity_line(counts)}")
    ordered = report.ordered_findings()
    for finding in ordered[:SUMMARY_MAX_FINDINGS]:
        source = f" [{finding.source.value} {finding.confidence:.2f}]" if finding.confidence else ""
        lines.append(f"  {finding.severity.value.upper():<8} {finding.rule_id}{source}")
        lines.append(f"           {_clip(finding.message, 2 * (SUMMARY_WIDTH - 11))}")
        variants = finding.evidence.get("variants")
        if isinstance(variants, list):
            examples = [v for v in variants if isinstance(v, dict) and v.get("payload")]
            lines.extend(
                f"           e.g. {_clip(example['payload'], SUMMARY_WIDTH - 16)}"
                for example in examples[:SUMMARY_MAX_EXAMPLES]
            )
    if len(ordered) > SUMMARY_MAX_FINDINGS:
        lines.append(f"  … {len(ordered) - SUMMARY_MAX_FINDINGS} more findings in the full report")
    banners = _banner_lines(report)
    if banners:
        lines.append("")
        lines.extend(_clip(banner.replace("**", ""), 200) for banner in banners)
    lines.append("")
    lines.append(
        f"{len(report.limitations)} limitation(s) and all evidence: run again with --format md"
    )
    return "\n".join(lines) + "\n"


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def workflow_annotations(report: Report) -> list[str]:
    """GitHub workflow commands for this report's findings, most severe first."""
    lines: list[str] = []
    for finding in report.ordered_findings():
        command = {
            Severity.escalate: "error",
            Severity.flag: "warning",
            Severity.info: "notice",
        }[finding.severity]
        title = _escape_property(f"{report.module}: {finding.rule_id}")
        message = _escape_data(finding.message)
        if finding.location:
            message = f"{message} ({_escape_data(finding.location)})"
        lines.append(f"::{command} title={title}::{message}")
    return lines


def append_step_summary(text: str, path: Path | None) -> bool:
    """Append rendered Markdown to a GitHub step summary file."""
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
    return True


def findings_from_json(payload: str | bytes) -> Sequence[Finding]:
    """Parse a list of findings, used by ``--compare`` and by tests."""
    raw: Iterable[Any] = json.loads(payload)
    return [Finding.model_validate(item) for item in raw]
