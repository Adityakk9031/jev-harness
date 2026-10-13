from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.modules.guard_bench.conftest import MakeProject, hook_command
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.report import Report

runner = CliRunner()

TINY = (
    "version: 1\npayloads:\n"
    "  - {id: wipe, category: destructive_file_ops, intent: wipe, command: 'rm -rf ./a', "
    "disguises: [variable]}\n"
    "  - {id: status, category: benign_control, intent: look, command: 'git status', "
    "expected: allow, disguises: []}\n"
)


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.yaml"
    path.write_text(TINY, encoding="utf-8")
    return path


def invoke(project: Path, corpus: Path, *extra: str):
    return runner.invoke(
        app,
        [
            "guard", "run", "--project", str(project), "--corpus", str(corpus),
            "--no-default-corpus", "--no-user-settings", "--no-managed-settings", *extra,
        ],
    )  # fmt: skip


def test_dry_run_executes_nothing(
    make_project: MakeProject, tiny_corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "ran.txt"
    monkeypatch.setenv("JEV_SENTINEL", str(sentinel))
    project = make_project(hooks=["sentinel_guard.py"])
    result = invoke(project, tiny_corpus, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "dry run: nothing was executed." in result.output
    assert "payloads:       3" in result.output
    assert not sentinel.exists()


def test_bypass_exits_one_and_a_clean_run_exits_zero(
    make_project: MakeProject, tiny_corpus: Path
) -> None:
    naive = invoke(make_project(hooks=["naive_guard.py"]), tiny_corpus)
    assert naive.exit_code == 1
    assert "guard.expected_block_allowed" in naive.output

    strict = invoke(make_project(hooks=["strict_guard.py"]), tiny_corpus)
    assert strict.exit_code == 0, strict.output
    assert "_None._" in strict.output


def test_fail_on_escalate_ignores_flag_findings(
    make_project: MakeProject, tiny_corpus: Path
) -> None:
    project = make_project(hooks=["overblock_guard.py"])
    assert invoke(project, tiny_corpus).exit_code == 1  # benign_blocked is a flag
    assert invoke(project, tiny_corpus, "--fail-on", "escalate").exit_code == 0


def test_output_json_and_compare_round_trip(
    make_project: MakeProject, tiny_corpus: Path, tmp_path: Path
) -> None:
    baseline_path = tmp_path / "baseline.json"
    first = invoke(
        make_project(hooks=["naive_guard.py"]),
        tiny_corpus,
        "--format",
        "json",
        "-o",
        str(baseline_path),
    )
    assert first.exit_code == 1
    assert Report.model_validate(json.loads(baseline_path.read_text())).module == "guard"

    second = invoke(
        make_project(hooks=["strict_guard.py"]), tiny_corpus, "--compare", str(baseline_path)
    )
    assert second.exit_code == 0
    assert "now stopped" in second.output


def test_github_format_emits_annotations(make_project: MakeProject, tiny_corpus: Path) -> None:
    result = invoke(make_project(hooks=["naive_guard.py"]), tiny_corpus, "--format", "github")
    assert "::error title=guard%3A guard.expected_block_allowed" in result.output


def test_extra_guard_command(make_project: MakeProject, tiny_corpus: Path) -> None:
    project = make_project()
    result = invoke(project, tiny_corpus, "--guard", hook_command("strict_guard.py"))
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--judge", "anthropic"], "ANTHROPIC_API_KEY"),
        (["--judge", "bogus"], "unknown --judge"),
        (["--judge-sample", "0"], "--judge-sample must be at least 1"),
        (["--hook-timeout", "0"], "must be positive"),
        (["--format", "pdf"], "unknown format"),
        (["--fail-on", "never"], "unknown --fail-on"),
    ],
)
def test_bad_arguments_exit_two(
    make_project: MakeProject, tiny_corpus: Path, args: list[str], message: str
) -> None:
    result = invoke(make_project(hooks=["strict_guard.py"]), tiny_corpus, *args)
    assert result.exit_code == 2
    assert message in result.output


def test_missing_compare_file_and_project_exit_two(
    make_project: MakeProject, tiny_corpus: Path, tmp_path: Path
) -> None:
    project = make_project(hooks=["strict_guard.py"])
    assert invoke(project, tiny_corpus, "--compare", str(tmp_path / "nope.json")).exit_code == 2
    assert invoke(tmp_path / "missing", tiny_corpus).exit_code == 2
