"""Report rendering tests.

The Markdown rendering is snapshot-tested: it is the artifact users read in CI,
and it must be byte-stable between runs so a diff means something changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion
from tests.conftest import example_report

from jev_harness.core.check import Finding, Severity, Source
from jev_harness.core.config import FailOn
from jev_harness.core.report import (
    OutputFormat,
    Report,
    append_step_summary,
    workflow_annotations,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("md", OutputFormat.md),
        ("markdown", OutputFormat.md),
        ("JSON", OutputFormat.json),
        ("github", OutputFormat.github),
        ("step-summary", OutputFormat.github),
    ],
)
def test_format_aliases(value: str, expected: OutputFormat) -> None:
    assert OutputFormat.parse(value) is expected


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        OutputFormat.parse("pdf")


def test_markdown_rendering_matches_snapshot(snapshot: SnapshotAssertion) -> None:
    assert example_report().render(OutputFormat.md) == snapshot


def test_markdown_is_stable_across_runs() -> None:
    assert example_report().render("md") == example_report().render("md")


def test_markdown_never_contains_the_generation_timestamp() -> None:
    text = example_report().render("md")
    assert "generated_at" not in text
    assert str(example_report().generated_at.year) not in text


def test_json_round_trips_and_carries_the_timestamp() -> None:
    report = example_report()
    payload = json.loads(report.render("json"))
    assert payload["module"] == "guard_bench"
    assert payload["generated_at"].startswith(str(report.generated_at.year))
    assert Report.model_validate(payload).findings == report.findings


def test_markdown_escapes_table_breakers() -> None:
    report = Report(
        module="guard_bench",
        title="pipes",
        summary="s",
        findings=[
            Finding(
                rule_id="guard.x",
                severity=Severity.escalate,
                message="allowed `rm -rf /` | piped",
                location="corpus:a|b",
            )
        ],
    )
    text = report.render("md")
    assert "\\| piped" in text
    assert "corpus:a\\|b" in text
    # The escaped cell must not have introduced an extra column.
    row = next(line for line in text.splitlines() if "corpus:a" in line)
    assert row.count("|") - row.count("\\|") == 7


def test_newlines_do_not_break_rows() -> None:
    report = Report(
        module="guard_bench",
        title="newlines",
        summary="s",
        findings=[
            Finding(
                rule_id="guard.x",
                severity=Severity.flag,
                message="line one\nline two",
            )
        ],
    )
    row = next(line for line in report.render("md").splitlines() if "line one" in line)
    assert "line two" in row


def test_banners_flag_approximated_and_simulated_results() -> None:
    emulated = Report(module="m", title="t", summary="s", meta={"matcher": "emulated"})
    assert "Approximation" in emulated.render("md")

    simulated = Report(module="m", title="t", summary="s", meta={"mode": "simulated"})
    assert "Simulated run" in simulated.render("md")

    real = Report(module="m", title="t", summary="s", meta={"matcher": "native", "mode": "real"})
    text = real.render("md")
    assert "Approximation" not in text
    assert "Simulated run" not in text


def test_empty_report_says_so_without_claiming_validity() -> None:
    text = Report(module="m", title="t", summary="s").render("md")
    assert "no findings" in text
    assert "_None recorded._" in text
    assert "Absence here is not a claim of validity." in text


def test_limitations_are_rendered() -> None:
    report = Report(module="m", title="t", summary="s", limitations=["5 trials is too few."])
    assert "- 5 trials is too few." in report.render("md")


def test_github_format_is_markdown_and_annotations_are_separate() -> None:
    report = example_report()
    assert report.render(OutputFormat.github) == report.render(OutputFormat.md)
    lines = workflow_annotations(report)
    # Property values escape ':' and ',' as well as '%', CR and LF.
    assert lines[0].startswith("::error title=guard_bench%3A guard.expected_block_allowed::")
    assert any(line.startswith("::warning title=") for line in lines)
    assert any(line.startswith("::notice title=") for line in lines)


def test_annotation_escaping_follows_the_workflow_command_rules() -> None:
    """Property and data escaping are different rules, and both are applied."""
    report = Report(
        module="guard_bench",
        title="t",
        summary="s",
        findings=[
            Finding(
                rule_id="guard.x",
                severity=Severity.escalate,
                message="100% bad, really:\nnext line",
                location="corpus:a:b,c",
            )
        ],
    )
    (line,) = workflow_annotations(report)
    assert line.startswith("::error title=guard_bench%3A guard.x::")
    # Data (the message) escapes only %, CR and LF; ':' and ',' stay literal.
    assert "100%25 bad, really:%0Anext line" in line
    assert "corpus:a:b,c" in line


def test_simulated_mode_meta_does_not_appear_as_a_finding() -> None:
    report = Report(
        module="compaction_survival",
        title="t",
        summary="s",
        meta={"mode": "simulated"},
        findings=[
            Finding(
                rule_id="compaction.source_lost",
                severity=Severity.flag,
                message="root CLAUDE.md canary lost after round 1",
                evidence={"round": 1},
            )
        ],
    )
    text = report.render("md")
    assert "Simulated run" in text
    assert "compaction.source_lost" in text


@pytest.mark.parametrize(
    ("severities", "fail_on", "expected"),
    [
        ([Severity.escalate], FailOn.flag, True),
        ([Severity.escalate], FailOn.escalate, True),
        ([Severity.flag], FailOn.flag, True),
        ([Severity.flag], FailOn.escalate, False),
        ([Severity.info], FailOn.flag, False),
        ([Severity.info], FailOn.escalate, False),
        ([], FailOn.flag, False),
    ],
)
def test_fail_on_thresholds(severities: list[Severity], fail_on: FailOn, expected: bool) -> None:
    report = Report(
        module="m",
        title="t",
        summary="s",
        findings=[
            Finding(rule_id=f"m.r{index}", severity=severity, message="x")
            for index, severity in enumerate(severities)
        ],
    )
    assert report.should_fail(fail_on) is expected


def test_severity_counts_and_selection() -> None:
    report = example_report()
    counts = report.severity_counts()
    assert counts[Severity.escalate] == 1
    assert counts[Severity.flag] == 1
    assert counts[Severity.info] == 1
    assert [f.rule_id for f in report.by_severity(Severity.escalate)] == [
        "guard.expected_block_allowed"
    ]


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report.md"
    text = example_report().write("md", target)
    assert target.read_text(encoding="utf-8") == text


def test_step_summary_appends_without_clobbering(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    append_step_summary("first", summary)
    append_step_summary("second\n", summary)
    assert summary.read_text(encoding="utf-8") == "first\nsecond\n"
    assert append_step_summary("never", None) is False


def test_judge_findings_render_their_confidence() -> None:
    report = Report(
        module="m",
        title="t",
        summary="s",
        findings=[
            Finding(
                rule_id="m.judged",
                severity=Severity.flag,
                message="judge thinks this is risky",
                source=Source.judge,
                confidence=0.42,
            )
        ],
    )
    assert "0.42" in report.render("md")


def test_evidence_block_is_sorted_json() -> None:
    report = Report(
        module="m",
        title="t",
        summary="s",
        findings=[
            Finding(
                rule_id="m.x",
                severity=Severity.flag,
                message="x",
                evidence={"b": 2, "a": 1},
            )
        ],
    )
    text = report.render("md")
    assert text.index('"a": 1') < text.index('"b": 2')
    assert '"```json' not in text


def test_summary_is_short_shows_headline_numbers_findings_and_examples() -> None:
    from jev_harness.core.check import Finding, Severity, Source
    from jev_harness.core.report import OutputFormat, Report

    findings = [
        Finding(
            rule_id="guard.intent_uncovered",
            severity=Severity.escalate,
            message=f"nothing stops intent {i}",
            location=f"intent-{i}",
            evidence={
                "variants": [
                    {"payload": f"X=rm; $X -rf ./t{i}"},
                    {"payload": "b"},
                    {"payload": "c"},
                ]
            },
        )
        for i in range(8)
    ] + [
        Finding(
            rule_id="reviewer.catch_not_confirmed",
            severity=Severity.flag,
            message="judge says unrelated",
            source=Source.judge,
            confidence=0.88,
        )
    ]
    report = Report(
        module="guard",
        title="guard_bench: hooks and permission rules",
        summary="331 of 503 dangerous payloads got through.",
        findings=findings,
        metrics={
            "payloads_total": 571,
            "guarded_overall": "172/503",
            "guarded.category.git": "0/97",
            "x.y": 1,
        },
        meta={"matcher": "emulated"},
        limitations=["one", "two"],
        headline=["guarded_overall", "guarded.category.*"],
    )
    text = report.render(OutputFormat.summary)
    lines = text.splitlines()
    assert lines[0] == "guard_bench: hooks and permission rules"
    assert "guarded_overall" in text
    assert "guarded.category.git" in text
    assert "payloads_total" not in text  # not a headline metric
    assert "Findings: 8 escalate · 1 flag · 0 info" in text
    assert text.count("ESCALATE") == 6  # capped
    assert "… 3 more findings in the full report" in text
    assert text.count("e.g. X=rm") == 6
    assert "e.g. c" not in text
    assert "Approximation." in text  # the emulated-matcher warning is never dropped
    assert "2 limitation(s) and all evidence: run again with --format md" in text
    assert len(lines) < 50
    assert OutputFormat.parse("summary") is OutputFormat.summary


def test_summary_falls_back_to_undotted_metrics_and_shows_judge_confidence() -> None:
    from jev_harness.core.check import Finding, Severity, Source
    from jev_harness.core.report import OutputFormat, Report

    report = Report(
        module="m",
        title="t",
        summary="s",
        findings=[
            Finding(
                rule_id="m.x",
                severity=Severity.flag,
                message="msg",
                source=Source.judge,
                confidence=0.75,
            )
        ],
        metrics={"a": 1, "b.c": 2},
    )
    text = report.render(OutputFormat.summary)
    assert "  a  1" in text
    assert "b.c" not in text
    assert "[judge 0.75]" in text
