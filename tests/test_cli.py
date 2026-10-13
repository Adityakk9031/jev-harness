"""CLI tests, including the exit-code contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jev_harness import __version__
from jev_harness.cli import app, main

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"jev-harness {__version__}"


def test_bare_invocation_prints_help_and_exits_two() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.stdout


def test_report_renders_markdown(result_file: Path) -> None:
    result = runner.invoke(app, ["report", str(result_file)])
    assert result.exit_code == 0
    assert "# guard_bench: hooks and permission rules" in result.stdout
    assert "guard.expected_block_allowed" in result.stdout


def test_report_renders_json(result_file: Path) -> None:
    result = runner.invoke(app, ["report", str(result_file), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["module"] == "guard_bench"
    assert len(payload["findings"]) == 3


def test_report_exit_code_reflects_fail_on(result_file: Path, clean_report_file: Path) -> None:
    default = runner.invoke(app, ["report", str(result_file), "--fail-on", "flag"])
    assert default.exit_code == 1

    strict = runner.invoke(app, ["report", str(result_file), "--fail-on", "escalate"])
    assert strict.exit_code == 1

    clean_flag = runner.invoke(app, ["report", str(clean_report_file), "--fail-on", "flag"])
    assert clean_flag.exit_code == 0
    assert "no findings" in clean_flag.stdout


def test_report_writes_to_a_file(tmp_path: Path, result_file: Path) -> None:
    target = tmp_path / "out" / "report.md"
    result = runner.invoke(app, ["report", str(result_file), "--output", str(target)])
    assert result.exit_code == 0
    assert target.is_file()
    assert "guard_bench" in target.read_text(encoding="utf-8")


def test_report_rejects_an_unknown_format(result_file: Path) -> None:
    result = runner.invoke(app, ["report", str(result_file), "--format", "pdf"])
    assert result.exit_code == 2
    assert "unknown format" in result.stderr


def test_report_rejects_an_unknown_fail_on(result_file: Path) -> None:
    result = runner.invoke(app, ["report", str(result_file), "--fail-on", "sometimes"])
    assert result.exit_code == 2


def test_report_rejects_a_non_report_json(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text('{"hello": "world"}')
    result = runner.invoke(app, ["report", str(path)])
    assert result.exit_code == 2
    assert "not a jev-harness result" in result.stderr


def test_missing_file_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["jev-harness", "report", str(tmp_path / "absent.json")])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2


def test_github_format_appends_to_the_step_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result_file: Path
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    result = runner.invoke(app, ["report", str(result_file), "--format", "github"])
    # Rendering never fails a build on its own; --fail-on is the opt-in.
    assert result.exit_code == 0
    assert "::error title=guard_bench%3A guard.expected_block_allowed::" in result.stdout
    written = summary.read_text(encoding="utf-8")
    assert "# guard_bench: hooks and permission rules" in written
    # Workflow commands must not leak into the summary file.
    assert "::error" not in written


def test_markdown_format_omits_annotations_unless_asked(result_file: Path) -> None:
    plain = runner.invoke(app, ["report", str(result_file)])
    asked = runner.invoke(app, ["report", str(result_file), "--annotations"])
    assert "::error" not in plain.stdout
    assert "::error" in asked.stdout


def test_github_format_emits_annotations_without_a_step_summary(
    monkeypatch: pytest.MonkeyPatch, result_file: Path
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    result = runner.invoke(app, ["report", str(result_file), "--format", "github"])
    assert result.exit_code == 0
    assert "::error title=guard_bench%3A guard.expected_block_allowed::" in result.stdout


def test_config_show_reports_defaults() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "config_file:" in result.stdout
    assert "judge: none" in result.stdout
    assert "min_samples: 30" in result.stdout
    assert "max_cost_usd: (unset)" in result.stdout


def test_config_show_never_prints_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "sk-should-never-appear"
    monkeypatch.setenv("JEV_API_KEY", secret)
    monkeypatch.setenv("JEV_JUDGE", "jev")
    text = runner.invoke(app, ["config", "show"]).stdout
    as_json = runner.invoke(app, ["config", "show", "--json"]).stdout
    assert secret not in text
    assert secret not in as_json
    assert "jev_api_key: <set>" in text
    assert json.loads(as_json)["settings"]["jev_api_key"] == "<set>"


def test_config_show_uses_a_config_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "jev-harness.yml"
    config.write_text("min_samples: 7\nfail_on: escalate\n")
    result = runner.invoke(app, ["--config", str(config), "config", "show"])
    assert result.exit_code == 0
    assert "min_samples: 7" in result.stdout
    assert "fail_on: escalate" in result.stdout
    assert str(config) in result.stdout


def test_invalid_config_exits_two(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = tmp_path / "jev-harness.yml"
    config.write_text("min_samples: not-a-number\n")
    monkeypatch.setattr(sys, "argv", ["jev-harness", "--config", str(config), "config", "show"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2


# --------------------------------------------------------------------- sessions


def claude_root() -> Path:
    return Path(__file__).parent / "fixtures" / "claude_code"


def test_sessions_list_reads_the_requested_root() -> None:
    result = runner.invoke(
        app,
        ["sessions", "list", "--agent", "claude-code", "--root", str(claude_root())],
    )
    assert result.exit_code == 0
    assert "claude_code:demo-basic" in result.stdout
    assert "basic session" in result.stdout  # the custom title
    assert "demo-basic.jsonl" in result.stdout  # the path, so it can be exported


def test_sessions_list_json_is_machine_readable() -> None:
    result = runner.invoke(
        app, ["sessions", "list", "-a", "claude-code", "--root", str(claude_root()), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["agents"] == ["claude-code"]
    assert {session["id"] for session in payload["sessions"]} == {
        "demo-basic",
        "demo-none",
        "demo-tools",
    }
    assert all(session["agent"] == "claude_code" for session in payload["sessions"])


def test_sessions_list_root_requires_an_agent(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sessions", "list", "--root", str(tmp_path)])
    assert result.exit_code == 2
    assert "--root needs --agent" in result.stderr


def test_sessions_list_reports_an_empty_root(tmp_path: Path) -> None:
    result = runner.invoke(app, ["sessions", "list", "-a", "codex", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert f"no sessions under {tmp_path}" in result.stdout


def test_sessions_list_rejects_an_unknown_agent() -> None:
    result = runner.invoke(app, ["sessions", "list", "-a", "cursor"])
    assert result.exit_code == 2
    assert "unknown importer" in result.stderr


def test_sessions_list_refuses_to_scrape_contextleleo() -> None:
    result = runner.invoke(app, ["sessions", "list", "-a", "contextleleo"])
    assert result.exit_code == 2
    assert "machine-readable" in result.stderr


def test_sessions_show_summarizes_and_previews() -> None:
    result = runner.invoke(
        app,
        [
            "sessions",
            "show",
            "demo-basic",
            "-a",
            "claude-code",
            "--root",
            str(claude_root()),
            "--turns",
            "2",
        ],
    )
    assert result.exit_code == 0
    assert "session:   demo-basic" in result.stdout
    assert "version:   2.1.221" in result.stdout
    assert "turns:     3" in result.stdout
    assert "preview: first 2 of 3 turns" in result.stdout
    assert "[0] user:" in result.stdout
    assert "Session started" in result.stdout
    # The third turn is deliberately outside the preview window.
    assert "CANARY_BASIC" not in result.stdout


def test_sessions_show_json_is_a_valid_trace() -> None:
    result = runner.invoke(
        app,
        [
            "sessions",
            "show",
            "demo-basic",
            "-a",
            "claude-code",
            "--root",
            str(claude_root()),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["agent"] == "claude_code"
    assert payload["session_id"] == "demo-basic"
    assert len(payload["turns"]) == 3


def test_sessions_show_missing_session_exits_two() -> None:
    result = runner.invoke(
        app,
        ["sessions", "show", "absent", "-a", "claude-code", "--root", str(claude_root())],
    )
    assert result.exit_code == 2
    assert "no Claude Code session" in result.stderr


def test_sessions_root_is_rejected_for_contextleleo(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["sessions", "show", "whatever", "-a", "contextleleo", "--root", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "--root applies to" in result.stderr


def test_sessions_export_writes_a_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "trace.json"
    result = runner.invoke(
        app,
        [
            "sessions",
            "export",
            "demo-tools",
            "-a",
            "claude-code",
            "--root",
            str(claude_root()),
            "--output",
            str(target),
        ],
    )
    assert result.exit_code == 0
    assert f"wrote {target}" in result.stdout
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["session_id"] == "demo-tools"
    assert payload["turns"]


def test_sessions_export_defaults_to_stdout() -> None:
    result = runner.invoke(
        app,
        ["sessions", "export", "demo-basic", "-a", "claude-code", "--root", str(claude_root())],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["session_id"] == "demo-basic"
