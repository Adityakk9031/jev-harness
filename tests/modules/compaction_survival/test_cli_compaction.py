from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.report import Report

runner = CliRunner()


def invoke(project: Path, *args: str):
    return runner.invoke(app, ["compaction", "run", "--project", str(project), *args])


def test_dry_run_lists_sources_and_writes_nothing(claude_project: Path) -> None:
    before = sorted(p.name for p in claude_project.rglob("*"))
    result = invoke(claude_project, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "root" in result.output
    assert "scoped-py" in result.output
    assert "no agent is called in simulated mode" in result.output
    assert "dry run: nothing was executed" in result.output
    assert sorted(p.name for p in claude_project.rglob("*")) == before


def test_real_dry_run_states_the_call_count_without_running_anything(claude_project: Path) -> None:
    result = invoke(claude_project, "--mode", "real", "--rounds", "3", "--trials", "5", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "agent calls: 5 trial(s)" not in result.output
    assert "agent calls: " in result.output
    assert "fresh session" in result.output
    assert "cost: unknown until run" in result.output


def test_simulated_run_is_labelled_and_compares_strategies(claude_project: Path) -> None:
    result = invoke(
        claude_project, "--strategy", "keep-recent", "--strategy", "claude-code-documented",
        "--format", "json",
    )  # fmt: skip
    assert result.exit_code == 1  # keep-recent loses everything, which is a finding
    report = Report.model_validate(json.loads(result.output))
    assert report.meta["mode"] == "simulated"
    assert report.meta["strategies"] == "keep-recent,claude-code-documented"
    assert report.metrics["strategy.keep-recent.survive_all_trials_after_3_rounds"] == "0/5 sources"
    assert report.metrics["strategy.claude-code-documented.survive_all_trials_after_3_rounds"] == (
        "2/5 sources"
    )
    assert "Simulated" in report.render("md")


def test_a_summarizer_that_preserves_everything_exits_zero(claude_project: Path) -> None:
    result = invoke(
        claude_project,
        "--summarizer", "tests.modules.compaction_survival.summarizers:keep_codewords",
        "--trials", "3",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "_None._" in result.output


def test_output_file_and_fail_on(claude_project: Path, tmp_path: Path) -> None:
    out = tmp_path / "c.md"
    result = invoke(
        claude_project, "--strategy", "keep-recent", "-o", str(out), "--fail-on", "escalate"
    )
    assert result.exit_code == 1  # behavior_regression is an escalate
    assert out.read_text().startswith("# compaction: what survives")


def test_real_mode_needs_a_cap_and_the_claude_cli(claude_project: Path) -> None:
    no_cap = invoke(claude_project, "--mode", "real")
    assert no_cap.exit_code == 2
    assert "--max-cost USD and/or --max-runs N" in no_cap.output
    no_cli = invoke(claude_project, "--mode", "real", "--max-runs", "1000")
    assert no_cli.exit_code == 2
    assert "claude executable was not found" in no_cli.output or "agent calls" in no_cli.output


def test_codex_real_mode_is_refused(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("rules")
    result = invoke(tmp_path, "--agent", "codex", "--mode", "real", "--max-runs", "10")
    assert result.exit_code == 2
    assert "claude-code only in v1" in result.output


def test_codex_sources_work_in_simulated_mode(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("rules")
    result = invoke(tmp_path, "--agent", "codex", "--strategy", "extractive", "--format", "json")
    assert result.exit_code in (0, 1)
    assert Report.model_validate(json.loads(result.output)).meta["agent"] == "codex"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--judge", "jev"], "--judge must be 'none'"),
        (["--rounds", "0"], "--rounds must be at least 1"),
        (["--trials", "0"], "--trials must be at least 1"),
        (["--mode", "magic"], "--mode must be"),
        (["--canary-position", "middle"], "--canary-position"),
        (["--strategy", "bogus"], "unknown strategy"),
        (["--summarizer", "nope"], "package.module:function"),
        (["--floor", "2"], "--floor"),
        (["--format", "pdf"], "unknown format"),
    ],
)
def test_bad_arguments_exit_two(claude_project: Path, args: list[str], message: str) -> None:
    result = invoke(claude_project, *args)
    assert result.exit_code == 2
    assert message in result.output


def test_a_project_with_no_instructions_exits_two(tmp_path: Path) -> None:
    result = invoke(tmp_path)
    assert result.exit_code == 2
    assert "no instruction sources found" in result.output
