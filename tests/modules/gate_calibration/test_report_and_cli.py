from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.modules.gate_calibration.gen import Level, gate_rows, write_jsonl
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.check import Severity
from jev_harness.core.report import Report
from jev_harness.modules import gate_calibration
from jev_harness.modules.gate_calibration.data import GateRow

pytest.importorskip("scipy")
runner = CliRunner()


def report_for(levels: list[Level], tmp_path: Path, **kwargs: object) -> Report:
    log = write_jsonl(
        gate_rows(levels, run_decisions=int(kwargs.pop("run_decisions", 0))), tmp_path / "l.jsonl"
    )  # type: ignore[call-overload]
    return gate_calibration.build_report(gate_calibration.load(log), **kwargs)  # type: ignore[arg-type]


def ids(report: Report) -> dict[str, list[Severity]]:
    out: dict[str, list[Severity]] = {}
    for f in report.findings:
        out.setdefault(f.rule_id, []).append(f.severity)
    return out


def test_well_calibrated_gate_that_meets_its_target_produces_no_findings(tmp_path: Path) -> None:
    """Must-not-flag: calibrated by construction, and safe enough for a 2% target."""
    report = report_for([(0.99, 1000, 995)], tmp_path, miss_target=0.02, skip_decision="skip")
    assert report.findings == []
    assert report.metrics["recommended_threshold"] == 0.99
    assert report.metrics["ece"] == pytest.approx(0.005, abs=1e-4)


def test_overconfident_gate_is_flagged_per_bucket_and_overall(tmp_path: Path) -> None:
    report = report_for([(0.95, 100, 60)], tmp_path)
    found = ids(report)
    assert found["calibrate.overconfident_bucket"] == [Severity.flag]
    assert found["calibrate.ece_above_target"] == [Severity.flag]
    assert found["calibrate.miss_bound_breach"] == [Severity.escalate]
    bucket = next(f for f in report.findings if f.rule_id == "calibrate.overconfident_bucket")
    assert bucket.evidence["accuracy"] == pytest.approx(0.60)
    assert report.should_fail(gate_calibration_fail("escalate"))


def gate_calibration_fail(name: str):
    from jev_harness.core.config import FailOn

    return FailOn(name)


def test_underconfident_gate_has_error_but_no_overconfident_bucket(tmp_path: Path) -> None:
    report = report_for([(0.65, 100, 90)], tmp_path, miss_target=0.2)
    found = ids(report)
    assert "calibrate.overconfident_bucket" not in found
    assert "calibrate.ece_above_target" in found


def test_too_few_labels_downgrade_the_error_finding_and_ask_for_more(tmp_path: Path) -> None:
    report = report_for([(0.95, 10, 6)], tmp_path)
    found = ids(report)
    assert found["calibrate.ece_above_target"] == [Severity.info]
    assert Severity.flag in found["calibrate.insufficient_labels"]
    message = next(
        f.message for f in report.findings if f.rule_id == "calibrate.insufficient_labels"
    )
    assert "only 10 labelled" in message


def test_zero_misses_but_few_labels_says_how_many_more(tmp_path: Path) -> None:
    report = report_for([(0.99, 100, 100)], tmp_path)
    finding = next(f for f in report.findings if f.rule_id == "calibrate.insufficient_labels")
    assert finding.evidence["additional_labels"] > 0
    assert "more labelled skips" in finding.message
    assert "recommended_threshold" in report.metrics
    assert str(report.metrics["recommended_threshold"]).startswith("none")


def test_explicit_threshold_is_evaluated_and_breach_is_escalated(tmp_path: Path) -> None:
    report = report_for(
        [(0.7, 200, 180), (0.9, 200, 196), (0.99, 400, 400)],
        tmp_path,
        run_decisions=200,
        threshold=0.7,
        miss_target=0.02,
    )
    breach = next(f for f in report.findings if f.rule_id == "calibrate.miss_bound_breach")
    assert breach.location == "threshold:0.7"
    assert breach.evidence["misses"] == 24
    assert breach.severity is Severity.escalate
    assert "threshold.0.7" in report.metrics
    ok = report_for(
        [(0.7, 200, 180), (0.9, 200, 196), (0.99, 400, 400)],
        tmp_path,
        run_decisions=200,
        threshold=0.9,
        miss_target=0.02,
    )
    assert not [f for f in ok.findings if f.rule_id == "calibrate.miss_bound_breach"]


def test_invalid_parameters_are_config_errors(tmp_path: Path) -> None:
    from jev_harness.core.errors import ConfigError

    with pytest.raises(ConfigError, match="--target"):
        report_for([(0.9, 5, 5)], tmp_path, miss_target=2.0)
    with pytest.raises(ConfigError, match="--bins"):
        report_for([(0.9, 5, 5)], tmp_path, bins=1)


def test_unmatched_and_blank_labels_are_reported_as_limitations(tmp_path: Path) -> None:
    log = write_jsonl(gate_rows([(0.9, 40, 40)], labeled=False), tmp_path / "l.jsonl")
    labels = tmp_path / "labels.csv"
    labels.write_text("id,label\nr00001,skip\nr00002,\nghost,skip\n", encoding="utf-8")
    report = gate_calibration.build_report(gate_calibration.load(log, labels))
    text = "\n".join(report.limitations)
    assert "matched no log row" in text
    assert "no label yet" in text
    assert "39 of 40 rows have no label" in text


def test_weighted_labels_are_disclosed(tmp_path: Path) -> None:
    rows = [GateRow.model_validate(r) for r in gate_rows([(0.9, 60, 54)])]
    weighted = [r.model_copy(update={"weight": 2.0 if i % 2 else 1.0}) for i, r in enumerate(rows)]
    report = gate_calibration.build_report(gate_calibration.LoadedLog(weighted, []))
    assert report.meta["weighted"] is True
    assert any("Kish" in line for line in report.limitations)


# ------------------------------------------------------------------------ drift


def drift_log(tmp_path: Path, *, after: int) -> Path:
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    before = gate_rows([(0.85, 200, 170)], start=datetime(2026, 1, 1, tzinfo=UTC), prefix="a")
    later = gate_rows([(0.85, 200, after)], start=cutoff, prefix="b")
    return write_jsonl(before + later, tmp_path / "drift.jsonl")


def test_drift_report_flags_a_real_shift_and_not_a_stable_gate(tmp_path: Path) -> None:
    for after, expected in ((120, 1), (170, 0)):
        log = drift_log(tmp_path, after=after)
        loaded = gate_calibration.load(log)
        a, b, description = gate_calibration.split_windows(
            loaded.rows, datetime(2026, 6, 1, tzinfo=UTC), None
        )
        report = gate_calibration.build_drift_report(a, b, description=description)
        assert len(report.findings) == expected
        assert report.metrics["window_a_rows"] == report.metrics["window_b_rows"] == 200


# -------------------------------------------------------------------------- CLI


def invoke(*args: str):
    return runner.invoke(app, ["calibrate", *args])


def test_cli_sheet_then_report_round_trip(tmp_path: Path) -> None:
    log = write_jsonl(
        gate_rows([(0.55, 300, 300), (0.99, 40, 40)], labeled=False), tmp_path / "log.jsonl"
    )
    sheet = tmp_path / "sheet.csv"
    made = invoke("sheet", str(log), "--out", str(sheet), "--n", "60")
    assert made.exit_code == 0, made.output
    assert "wrote" in made.output

    lines = sheet.read_text().splitlines()
    header = lines[0].split(",")
    label_at = header.index("label")
    filled = [lines[0]]
    for line in lines[1:]:
        cells = line.split(",")
        cells[label_at] = "skip"
        filled.append(",".join(cells))
    sheet.write_text("\n".join(filled) + "\n")

    done = invoke("report", str(log), "--labels", str(sheet), "--format", "json", "--target", "0.2")
    # Every sampled row was labelled "skip", so a gate that said 55% is badly underconfident
    # and the report correctly raises a finding (exit 1).
    assert done.exit_code == 1, done.output
    report = Report.model_validate(json.loads(done.output))
    assert report.metrics["labeled"] == 60
    assert report.meta["weighted"] is True


def test_cli_report_exit_codes_and_outputs(tmp_path: Path) -> None:
    bad = write_jsonl(gate_rows([(0.95, 100, 60)]), tmp_path / "bad.jsonl")
    good = write_jsonl(gate_rows([(0.99, 1000, 995)]), tmp_path / "good.jsonl")
    assert invoke("report", str(bad)).exit_code == 1
    assert invoke("report", str(bad), "--fail-on", "escalate").exit_code == 1
    assert invoke("report", str(good), "--target", "0.02").exit_code == 0
    out = tmp_path / "r.md"
    assert invoke("report", str(good), "--target", "0.02", "-o", str(out)).exit_code == 0
    assert out.read_text().startswith("# calibrate: gate confidence")


def test_cli_dry_run_scores_nothing(tmp_path: Path) -> None:
    log = write_jsonl(gate_rows([(0.9, 10, 8)]), tmp_path / "l.jsonl")
    result = invoke("report", str(log), "--dry-run")
    assert result.exit_code == 0
    assert "labelled:        10" in result.output
    assert "dry run: nothing was scored." in result.output
    assert "Findings" not in result.output


def test_cli_drift(tmp_path: Path) -> None:
    log = drift_log(tmp_path, after=120)
    ok = invoke("drift", str(log), "--split", "2026-06-01T00:00:00Z")
    assert ok.exit_code == 1
    assert "calibrate.drift_detected" in ok.output
    quiet = invoke("drift", str(drift_log(tmp_path, after=170)), "--split", "2026-06-01T00:00:00Z")
    assert quiet.exit_code == 0
    dry = invoke("drift", str(log), "--split", "2026-06-01T00:00:00Z", "--dry-run")
    assert "window A: 200 rows" in dry.output


def test_cli_drift_against_a_second_log(tmp_path: Path) -> None:
    first = write_jsonl(gate_rows([(0.85, 200, 170)], prefix="a"), tmp_path / "a.jsonl")
    second = write_jsonl(gate_rows([(0.85, 200, 120)], prefix="b"), tmp_path / "b.jsonl")
    assert invoke("drift", str(first), "--against", str(second)).exit_code == 1


@pytest.mark.parametrize(
    "args",
    [
        ["report", "{log}", "--judge", "jev"],
        ["report", "{log}", "--target", "5"],
        ["report", "{log}", "--format", "pdf"],
        ["report", "{missing}"],
        ["drift", "{log}"],
        ["drift", "{log}", "--split", "not-a-date"],
        ["drift", "{log}", "--split", "2026-06-01T00:00:00"],
        ["sheet", "{log}", "--out", "{out}", "--n", "0"],
    ],
)
def test_cli_errors_exit_two(tmp_path: Path, args: list[str]) -> None:
    log = write_jsonl(gate_rows([(0.9, 10, 8)], labeled=False), tmp_path / "l.jsonl")
    filled = [a.format(log=log, missing=tmp_path / "x.jsonl", out=tmp_path / "s.csv") for a in args]
    assert invoke(*filled).exit_code == 2
