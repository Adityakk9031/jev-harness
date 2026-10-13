"""Headless agent runners: start a session, resume one, record, and replay.

Only the flags recorded in ``docs/AGENT_CLIS.md`` are used, and ``plan()``
returns the exact argv so a test can pin each one. The CLIs are not installed
on every machine, so:

* ``available()`` reports whether the executable is on PATH, and ``version()``
  asks it (the compaction bench needs the version, since behaviour is
  version-dependent).
* Every run can be recorded, and :class:`ReplayRunner` reproduces it offline,
  which is how unit tests exercise modules that call agents without a network.
* A replay that cannot find its recording raises instead of returning an empty
  transcript (the same rule the importers follow).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import BudgetExceededError, HarnessError
from jev_harness.core.importers.codex import CodexImporter
from jev_harness.core.trace import Trace, Turn, TurnRole

DEFAULT_TIMEOUT_SECONDS = 600.0
TAIL_CHARS = 2000

SubprocessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class RunnerError(HarnessError):
    """An agent CLI could not be run, or its output could not be understood."""


class RunStatus(StrEnum):
    """How a run ended."""

    ok = "ok"
    failed = "failed"
    timeout = "timeout"
    unavailable = "unavailable"
    budget_exceeded = "budget_exceeded"


class RunResult(BaseModel):
    """Everything a module needs to know about one agent invocation."""

    model_config = ConfigDict(extra="forbid")

    runner: str
    status: RunStatus
    #: Not stored in recordings (it holds an absolute temp path), so empty is valid.
    argv: list[str] = Field(default_factory=list)
    exit_status: int | None = None
    duration_seconds: float = 0.0
    cost_usd: float | None = Field(default=None, ge=0.0)
    session_id: str | None = None
    trace: Trace | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    raw: dict[str, Any] | None = None
    recorded: bool = False

    def ok(self) -> bool:
        return self.status is RunStatus.ok


class Budget(BaseModel):
    """Run and cost caps, enforced before and after each run (brief 2.9)."""

    model_config = ConfigDict(extra="forbid")

    max_cost_usd: float | None = Field(default=None, gt=0.0)
    max_runs: int | None = Field(default=None, ge=1)
    spent_usd: float = Field(default=0.0, ge=0.0)
    runs: int = 0
    unpriced_runs: int = 0

    def check(self, *, next_estimate_usd: float | None = None) -> None:
        """Refuse a run that would exceed a cap. Called *before* spending."""
        if self.max_runs is not None and self.runs >= self.max_runs:
            raise BudgetExceededError(
                f"run cap reached: {self.runs} of {self.max_runs} runs already used"
            )
        if self.max_cost_usd is None:
            return
        if next_estimate_usd is not None and self.spent_usd + next_estimate_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"cost cap would be exceeded: {self.spent_usd:.4f} spent + "
                f"{next_estimate_usd:.4f} estimated > {self.max_cost_usd:.4f} allowed"
            )

    def record(self, result: RunResult) -> None:
        """Account for a finished run."""
        self.runs += 1
        if result.cost_usd is None:
            self.unpriced_runs += 1
        else:
            self.spent_usd += result.cost_usd
        if self.max_cost_usd is not None and self.spent_usd > self.max_cost_usd:
            raise BudgetExceededError(
                f"cost cap exceeded after the fact: {self.spent_usd:.4f} spent > "
                f"{self.max_cost_usd:.4f} allowed. Raise --max-cost or reduce the run count."
            )

    def summary(self) -> str:
        """A one-line description for reports, honest about unknown costs."""
        parts = [f"runs={self.runs}"]
        if self.max_runs is not None:
            parts.append(f"max_runs={self.max_runs}")
        if self.max_cost_usd is None:
            parts.append("spent=unmetered")
        else:
            parts.append(f"spent_usd={self.spent_usd:.4f}")
            parts.append(f"max_cost_usd={self.max_cost_usd:.4f}")
        if self.unpriced_runs:
            parts.append(f"unpriced_runs={self.unpriced_runs}")
        return " ".join(parts)


class AgentRunner(Protocol):
    """What every runner provides."""

    name: str

    def available(self) -> bool:
        """Whether the agent executable can be found."""

    def version(self) -> str | None:
        """The agent's own version string, or ``None`` if it cannot be read."""

    def plan(self, prompt: str, *, workspace: Path, session: str | None = None) -> list[str]:
        """The argv this run would use, for ``--dry-run``."""

    async def run(
        self,
        prompt: str,
        *,
        workspace: Path,
        session: str | None = None,
        budget: Budget | None = None,
    ) -> RunResult:
        """Run the agent headlessly and return the result."""


class CliRunner:
    """Shared behaviour for agent CLIs driven as subprocesses."""

    name = "cli"

    def __init__(
        self,
        executable: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        model: str | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
        subprocess_runner: SubprocessRunner | None = None,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.extra_args = list(extra_args)
        self.env = dict(env) if env is not None else None
        self._subprocess: SubprocessRunner = (
            subprocess_runner if subprocess_runner is not None else subprocess.run
        )
        self._version: str | None = None

    # -------------------------------------------------------------- probing

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def version(self) -> str | None:
        """Ask the CLI for its version, once, caching the answer."""
        if self._version is not None:
            return self._version
        if not self.available():
            return None
        try:
            completed = self._subprocess(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        first_line = (completed.stdout or completed.stderr or "").strip().splitlines()
        self._version = first_line[0].strip() if first_line else None
        return self._version

    # -------------------------------------------------------------- running

    def plan(self, prompt: str, *, workspace: Path, session: str | None = None) -> list[str]:
        return self._argv(prompt, workspace=workspace, session=session)

    def _argv(self, prompt: str, *, workspace: Path, session: str | None) -> list[str]:
        raise NotImplementedError

    def _parse(self, result: RunResult, workspace: Path, prompt: str) -> RunResult:
        raise NotImplementedError

    async def run(
        self,
        prompt: str,
        *,
        workspace: Path,
        session: str | None = None,
        budget: Budget | None = None,
    ) -> RunResult:
        argv = self._argv(prompt, workspace=workspace, session=session)
        if budget is not None:
            budget.check()
        if not self.available():
            raise RunnerError(
                f"{self.executable!r} is not on PATH; install it or pass a recorded run "
                "to ReplayRunner instead"
            )

        started = time.monotonic()
        # subprocess.run in a thread keeps timeout handling identical to the
        # synchronous path that the tests exercise.
        try:
            completed = await asyncio.to_thread(
                self._subprocess,
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                cwd=str(workspace),
                env=self._env(),
            )
        except subprocess.TimeoutExpired:
            result = RunResult(
                runner=self.name,
                status=RunStatus.timeout,
                argv=argv,
                duration_seconds=time.monotonic() - started,
                stderr_tail=f"timed out after {self.timeout_seconds:g}s",
            )
            if budget is not None:
                budget.record(result)
            return result
        except OSError as exc:
            raise RunnerError(f"could not run {self.executable!r}: {exc}") from exc

        result = RunResult(
            runner=self.name,
            status=RunStatus.ok if completed.returncode == 0 else RunStatus.failed,
            argv=argv,
            exit_status=completed.returncode,
            duration_seconds=time.monotonic() - started,
            # Parse the *full* stdout: Claude's JSON envelope is one long line and Codex's
            # event stream is long, so parsing only the tail would lose both. Only the stored
            # copy is trimmed.
            stdout_tail=completed.stdout or "",
            stderr_tail=_tail(completed.stderr or ""),
        )
        result = self._parse(result, workspace, prompt)
        result.stdout_tail = _tail(result.stdout_tail)
        if budget is not None:
            budget.record(result)
        return result

    def _env(self) -> dict[str, str]:
        base = dict(os.environ)
        if self.env:
            base.update(self.env)
        return base


class ClaudeCodeRunner(CliRunner):
    """Drives ``claude -p`` (see docs/AGENT_CLIS.md for the flags we rely on)."""

    name = "claude-code"

    def __init__(
        self,
        executable: str = "claude",
        *,
        permission_mode: str | None = None,
        max_turns: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(executable, **kwargs)
        self.permission_mode = permission_mode
        self.max_turns = max_turns

    def _argv(self, prompt: str, *, workspace: Path, session: str | None) -> list[str]:
        argv = [self.executable, "-p", prompt, "--output-format", "json"]
        if self.model:
            argv += ["--model", self.model]
        if self.max_turns is not None:
            argv += ["--max-turns", str(self.max_turns)]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        if session:
            argv += ["--resume", session]
        return argv + self.extra_args

    def _parse(self, result: RunResult, workspace: Path, prompt: str) -> RunResult:
        payload = _last_json_object(result.stdout_tail)
        if payload is None:
            # Without the JSON envelope there is no cost, session id, or answer.
            # Say so rather than reporting an empty successful run.
            if result.ok():
                result.status = RunStatus.failed
                result.stderr_tail = (
                    f"{result.stderr_tail}\nunparseable --output-format json envelope"
                ).strip()
            return result

        result.raw = payload
        session_id = payload.get("session_id")
        if isinstance(session_id, str):
            result.session_id = session_id
        cost = payload.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            result.cost_usd = float(cost)
        result.trace = _trace_from_envelope(payload, prompt=prompt, agent=self.name)
        return result


class CodexRunner(CliRunner):
    """Drives ``codex exec --json`` (see docs/AGENT_CLIS.md)."""

    name = "codex"

    def __init__(
        self,
        executable: str = "codex",
        *,
        sandbox: str | None = None,
        approval_policy: str | None = "never",
        **kwargs: Any,
    ) -> None:
        super().__init__(executable, **kwargs)
        self.sandbox = sandbox
        self.approval_policy = approval_policy

    def _argv(self, prompt: str, *, workspace: Path, session: str | None) -> list[str]:
        argv = [self.executable, "exec"]
        argv += ["resume", session, prompt] if session else [prompt]
        argv += ["--json", "--cd", str(workspace)]
        if self.model:
            argv += ["--model", self.model]
        if self.sandbox:
            argv += ["--sandbox", self.sandbox]
        if self.approval_policy:
            argv += ["--ask-for-approval", self.approval_policy]
        return argv + self.extra_args

    def _parse(self, result: RunResult, workspace: Path, prompt: str) -> RunResult:
        if not result.ok():
            return result
        if not (result.stdout_tail or "").strip():
            result.status = RunStatus.failed
            result.stderr_tail = f"{result.stderr_tail}\nempty event stream".strip()
            return result

        importer = CodexImporter()
        try:
            trace = importer.parse_text(result.stdout_tail, source=f"{self.name} exec --json")
        except HarnessError as exc:
            result.status = RunStatus.failed
            result.stderr_tail = f"{result.stderr_tail}\n{exc}".strip()
            return result

        result.trace = trace
        result.session_id = trace.session_id
        usage = trace.meta.get("usage")
        if isinstance(usage, dict) and result.cost_usd is None:
            # Codex reports tokens, not dollars; a price table is needed to say
            # anything about cost, so cost stays unknown rather than guessed.
            result.cost_usd = None
        return result


# ------------------------------------------------------------------ replay


def recording_key(runner: str, prompt: str, *, label: str | None = None) -> str:
    """A stable file name for one recorded run."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    prefix = f"{runner}-{label}" if label else runner
    return f"{prefix}-{digest}"


def record_result(
    result: RunResult, directory: Path, *, prompt: str, label: str | None = None
) -> Path:
    """Write a run to disk so tests can replay it without the CLI.

    ``prompt`` is required rather than read from ``argv``: argument order is
    agent-specific and reading it back would silently key the recording wrong.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{recording_key(result.runner, prompt, label=label)}.json"
    # The argv is not stored: it contains an absolute temp workspace path.
    payload = result.model_dump(mode="json", exclude={"argv", "stdout_tail", "stderr_tail"})
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


class ReplayRunner:
    """Replays recorded runs. Used by every module test that needs an agent."""

    name = "replay"

    def __init__(
        self,
        directory: Path,
        *,
        for_runner: str = "claude-code",
        label: str | None = None,
    ) -> None:
        self.directory = directory
        #: Recordings are keyed by the runner that produced them, so a replay
        #: must say which agent it stands in for.
        self.for_runner = for_runner
        self.label = label
        self.calls: list[str] = []

    def available(self) -> bool:
        return self.directory.is_dir() and any(self.directory.glob("*.json"))

    def version(self) -> str | None:
        return "replay"

    def plan(self, prompt: str, *, workspace: Path, session: str | None = None) -> list[str]:
        return [f"replay:{recording_key(self.for_runner, prompt, label=self.label)}"]

    async def run(
        self,
        prompt: str,
        *,
        workspace: Path,
        session: str | None = None,
        budget: Budget | None = None,
    ) -> RunResult:
        if budget is not None:
            budget.check()
        self.calls.append(prompt)
        key = recording_key(self.for_runner, prompt, label=self.label)
        path = self.directory / f"{key}.json"
        if not path.is_file():
            raise RunnerError(
                f"no recording for this run: expected {path}. Recordings are keyed by "
                "runner, label and prompt; re-record with the live CLI or fix the prompt."
            )
        result = RunResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
        result.recorded = True
        result.argv = self.plan(prompt, workspace=workspace, session=session)
        if budget is not None:
            budget.record(result)
        return result


def get_runner(name: str, **kwargs: Any) -> AgentRunner:
    """Build a runner by name: ``claude-code``, ``codex``, ``replay``."""
    normalized = name.strip().lower().replace("_", "-")
    builders: dict[str, Callable[..., AgentRunner]] = {
        "claude-code": ClaudeCodeRunner,
        "claude": ClaudeCodeRunner,
        "codex": CodexRunner,
        "replay": ReplayRunner,
    }
    builder = builders.get(normalized)
    if builder is None:
        known = ", ".join(sorted(builders))
        raise RunnerError(f"unknown runner {name!r}; known runners: {known}")
    return builder(**kwargs)


# ------------------------------------------------------------------ helpers


def _tail(text: str) -> str:
    return text[-TAIL_CHARS:] if len(text) > TAIL_CHARS else text


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The JSON envelope is the last complete object on stdout."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _trace_from_envelope(payload: dict[str, Any], *, prompt: str, agent: str) -> Trace:
    """A minimal trace from a ``--output-format json`` envelope.

    The envelope holds the final answer, not the full transcript. Modules that
    need every turn should re-read the session with the Claude Code importer
    using ``RunResult.session_id``.
    """
    turns = [Turn(role=TurnRole.user, content=prompt, index=0)]
    result_text = payload.get("result")
    if isinstance(result_text, str) and result_text:
        turns.append(Turn(role=TurnRole.assistant, content=result_text, index=1))

    usage = payload.get("usage")
    if isinstance(usage, dict):
        output_tokens = usage.get("output_tokens")
        if isinstance(output_tokens, int) and len(turns) > 1:
            turns[-1] = turns[-1].model_copy(update={"token_count": output_tokens})

    session_id = payload.get("session_id")
    return Trace(
        agent=agent,
        session_id=session_id if isinstance(session_id, str) and session_id else "unknown-session",
        turns=turns,
        meta={
            "envelope_keys": sorted(payload.keys()),
            "is_complete_transcript": False,
            "note": "built from the CLI result envelope; re-import the session for every turn",
        },
    )
