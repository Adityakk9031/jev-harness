"""Runner tests.

The agent CLIs are not installed here, so the argv is pinned exactly (that is
the part we depend on) and runs are driven through an injected subprocess or a
recording.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.conftest import fixture_path

from jev_harness.core.errors import BudgetExceededError
from jev_harness.core.runner import (
    Budget,
    ClaudeCodeRunner,
    CodexRunner,
    ReplayRunner,
    RunnerError,
    RunResult,
    RunStatus,
    get_runner,
    record_result,
    recording_key,
)
from jev_harness.core.trace import TurnRole

WORKSPACE = Path("/tmp/ws-example")
ENVELOPE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "result": "The codeword is CANARY_BASIC.",
        "session_id": "session-123",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 1200, "output_tokens": 42},
    }
)


class FakeProcess:
    """Stands in for subprocess.run, so nothing is actually executed."""

    def __init__(
        self,
        *,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.raises = raises
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(argv), kwargs))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/agent")


# ---------------------------------------------------------------------- argv


def test_claude_code_argv_matches_the_documented_flags() -> None:
    runner = ClaudeCodeRunner()
    assert runner.plan("say hi", workspace=WORKSPACE) == [
        "claude",
        "-p",
        "say hi",
        "--output-format",
        "json",
    ]

    configured = ClaudeCodeRunner(
        model="claude-sonnet-5", max_turns=3, permission_mode="bypassPermissions"
    )
    assert configured.plan("go", workspace=WORKSPACE) == [
        "claude",
        "-p",
        "go",
        "--output-format",
        "json",
        "--model",
        "claude-sonnet-5",
        "--max-turns",
        "3",
        "--permission-mode",
        "bypassPermissions",
    ]


def test_claude_code_resume_and_extra_args() -> None:
    runner = ClaudeCodeRunner(extra_args=["--add-dir", "/tmp/extra"])
    argv = runner.plan("go", workspace=WORKSPACE, session="session-123")
    assert argv[-4:] == ["--resume", "session-123", "--add-dir", "/tmp/extra"]


def test_codex_argv_matches_the_documented_flags() -> None:
    runner = CodexRunner(sandbox="workspace-write")
    assert runner.plan("list files", workspace=WORKSPACE) == [
        "codex",
        "exec",
        "list files",
        "--json",
        "--cd",
        str(WORKSPACE),
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
    ]


def test_codex_resume_uses_the_resume_subcommand() -> None:
    runner = CodexRunner(model="gpt-5-codex")
    argv = runner.plan("carry on", workspace=WORKSPACE, session="abc")
    assert argv[:5] == ["codex", "exec", "resume", "abc", "carry on"]
    assert "--model" in argv


def test_unknown_runner_names_are_rejected() -> None:
    assert isinstance(get_runner("claude-code"), ClaudeCodeRunner)
    assert isinstance(get_runner("CLAUDE"), ClaudeCodeRunner)
    assert isinstance(get_runner("codex"), CodexRunner)
    with pytest.raises(RunnerError) as error:
        get_runner("cursor")
    assert "unknown runner 'cursor'" in str(error.value)
    assert "codex" in str(error.value)


# -------------------------------------------------------------------- probing


def test_available_and_version_are_read_from_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeProcess(stdout="2.1.221 (Claude Code)\n")
    runner = ClaudeCodeRunner(subprocess_runner=fake)
    assert runner.available() is False  # not installed in this environment
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/claude")
    assert runner.available() is True
    assert runner.version() == "2.1.221 (Claude Code)"
    assert runner.version() == "2.1.221 (Claude Code)"  # cached: one probe only
    assert [call[0] for call in fake.calls] == [["claude", "--version"]]


def test_version_is_none_when_the_probe_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/claude")
    failing = ClaudeCodeRunner(subprocess_runner=FakeProcess(stderr="boom", returncode=1))
    assert failing.version() is None
    broken = ClaudeCodeRunner(subprocess_runner=FakeProcess(raises=OSError("nope")))
    assert broken.version() is None


# ---------------------------------------------------------------------- runs


async def test_claude_code_run_parses_the_json_envelope(
    installed: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeProcess(stdout=ENVELOPE)
    runner = ClaudeCodeRunner(subprocess_runner=fake)
    result = await runner.run("say the codeword", workspace=tmp_path)

    assert result.ok()
    assert result.cost_usd == 0.0123
    assert result.session_id == "session-123"
    assert result.trace is not None
    assert [turn.role for turn in result.trace.turns] == [TurnRole.user, TurnRole.assistant]
    assert result.trace.turns[1].content == "The codeword is CANARY_BASIC."
    assert result.trace.turns[1].token_count == 42
    assert result.trace.meta["is_complete_transcript"] is False

    argv, kwargs = fake.calls[0]
    assert argv[0] == "claude"
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["check"] is False


async def test_unparseable_output_is_a_failure_not_an_empty_success(
    installed: None, tmp_path: Path
) -> None:
    runner = ClaudeCodeRunner(subprocess_runner=FakeProcess(stdout="not json at all"))
    result = await runner.run("go", workspace=tmp_path)
    assert result.status is RunStatus.failed
    assert result.trace is None
    assert "unparseable --output-format json envelope" in result.stderr_tail


async def test_failed_exit_status_is_preserved(installed: None, tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(subprocess_runner=FakeProcess(stdout="", stderr="boom", returncode=2))
    result = await runner.run("go", workspace=tmp_path)
    assert result.status is RunStatus.failed
    assert result.exit_status == 2
    assert "boom" in result.stderr_tail


async def test_codex_run_reuses_the_event_stream_parser(installed: None, tmp_path: Path) -> None:
    events = fixture_path("codex", "exec-events.jsonl").read_text()
    runner = CodexRunner(subprocess_runner=FakeProcess(stdout=events))
    result = await runner.run("summarize the repo", workspace=tmp_path)
    assert result.ok()
    assert result.trace is not None
    assert result.trace.session_id == "0199a213-81c0-7800-8aa1-bbab2a035a53"
    assert any(turn.tool_name == "shell" for turn in result.trace.turns)
    # Codex reports tokens, not dollars, so cost stays unknown rather than guessed.
    assert result.cost_usd is None


async def test_codex_empty_stream_is_a_failure(installed: None, tmp_path: Path) -> None:
    runner = CodexRunner(subprocess_runner=FakeProcess(stdout=""))
    result = await runner.run("go", workspace=tmp_path)
    assert result.status is RunStatus.failed
    assert "empty event stream" in result.stderr_tail


async def test_codex_unrecognized_stream_is_a_failure(installed: None, tmp_path: Path) -> None:
    runner = CodexRunner(subprocess_runner=FakeProcess(stdout='{"type":"mystery"}\n'))
    result = await runner.run("go", workspace=tmp_path)
    assert result.status is RunStatus.failed
    assert "no turns" in result.stderr_tail


async def test_timeout_is_reported_as_a_status(installed: None, tmp_path: Path) -> None:
    runner = ClaudeCodeRunner(
        subprocess_runner=FakeProcess(raises=subprocess.TimeoutExpired(cmd="claude", timeout=1)),
        timeout_seconds=1,
    )
    result = await runner.run("go", workspace=tmp_path)
    assert result.status is RunStatus.timeout
    assert "timed out after 1s" in result.stderr_tail


async def test_running_a_missing_cli_explains_the_alternative(tmp_path: Path) -> None:
    runner = ClaudeCodeRunner()
    with pytest.raises(RunnerError) as error:
        await runner.run("go", workspace=tmp_path)
    assert "not on PATH" in str(error.value)
    assert "ReplayRunner" in str(error.value)


# -------------------------------------------------------------------- budget


def test_budget_refuses_before_spending() -> None:
    budget = Budget(max_runs=1)
    budget.check()
    budget.record(_result(cost=0.01))
    with pytest.raises(BudgetExceededError, match="run cap reached"):
        budget.check()


def test_budget_refuses_when_the_estimate_would_break_the_cap() -> None:
    budget = Budget(max_cost_usd=0.05)
    budget.record(_result(cost=0.04))
    budget.check(next_estimate_usd=0.005)
    with pytest.raises(BudgetExceededError, match="would be exceeded"):
        budget.check(next_estimate_usd=0.02)


def test_budget_flags_an_overspend_after_the_fact() -> None:
    budget = Budget(max_cost_usd=0.01)
    with pytest.raises(BudgetExceededError, match="cap exceeded after the fact"):
        budget.record(_result(cost=0.02))


def test_budget_summary_is_honest_about_unpriced_runs() -> None:
    budget = Budget(max_cost_usd=1.0, max_runs=5)
    budget.record(_result(cost=0.25))
    budget.record(_result(cost=None))
    summary = budget.summary()
    assert "runs=2" in summary
    assert "unpriced_runs=1" in summary
    assert "spent_usd=0.2500" in summary
    assert Budget().summary() == "runs=0 spent=unmetered"


async def test_budget_is_enforced_through_a_run(installed: None, tmp_path: Path) -> None:
    budget = Budget(max_runs=1)
    runner = ClaudeCodeRunner(subprocess_runner=FakeProcess(stdout=ENVELOPE))
    await runner.run("go", workspace=tmp_path, budget=budget)
    assert budget.spent_usd == pytest.approx(0.0123)
    with pytest.raises(BudgetExceededError, match="run cap reached"):
        await runner.run("again", workspace=tmp_path, budget=budget)


# ---------------------------------------------------------- recording/replay


def _result(*, cost: float | None, runner: str = "claude-code") -> RunResult:
    return RunResult(
        runner=runner,
        status=RunStatus.ok,
        argv=["claude", "-p", "go"],
        cost_usd=cost,
    )


def test_recording_key_is_stable_and_prompt_specific() -> None:
    assert recording_key("claude-code", "go") == recording_key("claude-code", "go")
    assert recording_key("claude-code", "go") != recording_key("claude-code", "stop")
    assert recording_key("codex", "go", label="compaction").startswith("codex-compaction-")


async def test_record_then_replay_round_trips(installed: None, tmp_path: Path) -> None:
    recordings = tmp_path / "recordings"
    live = ClaudeCodeRunner(subprocess_runner=FakeProcess(stdout=ENVELOPE))
    budget = Budget()
    prompt = "say the codeword"
    result = await live.run(prompt, workspace=tmp_path, budget=budget)
    written = record_result(result, recordings, prompt=prompt)
    assert written.name == f"{recording_key('claude-code', prompt)}.json"

    replay = ReplayRunner(recordings)
    assert replay.available()
    assert replay.version() == "replay"
    assert replay.plan(prompt, workspace=tmp_path)[0].startswith("replay:")

    replayed = await replay.run(prompt, workspace=tmp_path, budget=budget)
    assert replayed.recorded is True
    assert replayed.trace is not None
    assert replayed.trace.turns[1].content == "The codeword is CANARY_BASIC."
    assert replay.calls == [prompt]


async def test_replay_without_a_recording_fails_loudly(tmp_path: Path) -> None:
    replay = ReplayRunner(tmp_path / "empty")
    assert replay.available() is False
    with pytest.raises(RunnerError) as error:
        await replay.run("never recorded", workspace=tmp_path)
    assert "no recording for this run" in str(error.value)
    assert "never recorded" not in str(error.value)  # prompts are not echoed back
