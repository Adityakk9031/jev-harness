"""Run PreToolUse hooks against payloads.

A payload reaches a hook only as JSON on stdin. The hook command itself is
user-configured and is started through ``/bin/sh -c`` exactly as the agent
starts it; the payload is never part of any command line.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from jev_harness.modules.guard_bench.corpus import Payload
from jev_harness.modules.guard_bench.decision import GuardDecision, Outcome
from jev_harness.modules.guard_bench.discovery import HookSpec

SESSION_ID = "jev-harness-bench"
MAX_REASON_CHARS = 300
#: Environment variables passed through to hooks by default. Everything else, API keys
#: included, is withheld unless the user opts in with ``--pass-env``.
BASE_ENV = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SHELL", "TERM")


def hook_applies(matcher: str, tool_name: str) -> bool:
    """Whether a hook's tool matcher selects this tool (regex on the tool name)."""
    if matcher in {"", "*"}:
        return True
    try:
        return re.fullmatch(matcher, tool_name) is not None
    except re.error:
        return matcher == tool_name


def hook_input(payload: Payload, cwd: Path) -> dict[str, Any]:
    """The PreToolUse stdin document: the documented fields and nothing invented."""
    return {
        "session_id": SESSION_ID,
        "cwd": str(cwd),
        "hook_event_name": "PreToolUse",
        "tool_name": payload.tool,
        "tool_input": payload.tool_input,
    }


def build_env(home: Path, project: Path, pass_env: list[str]) -> dict[str, str]:
    env = {name: os.environ[name] for name in BASE_ENV if name in os.environ}
    for name in pass_env:
        if name in os.environ:
            env[name] = os.environ[name]
    env["HOME"] = str(home)
    env["CLAUDE_PROJECT_DIR"] = str(project)
    return env


def _short(text: str) -> str:
    flattened = " ".join(text.split())
    return flattened[:MAX_REASON_CHARS]


def parse_hook_output(
    guard: str, exit_code: int, stdout: str, stderr: str, elapsed_ms: float
) -> GuardDecision:
    """Interpret a finished hook process the way Claude Code does.

    Exit 2 blocks. Exit 0 defers to a JSON decision on stdout, and an exit-0 hook with no
    decision has *no opinion* (it is not approval). Any other exit code is a non-blocking
    error, so the payload proceeds.
    """
    detail: dict[str, Any] = {"exit_code": exit_code, "elapsed_ms": round(elapsed_ms, 1)}
    if exit_code == 2:
        return GuardDecision(
            guard=guard,
            kind="hook",
            outcome=Outcome.block,
            reason=_short(stderr) or "hook exited 2",
            detail=detail,
        )
    if exit_code != 0:
        return GuardDecision(
            guard=guard,
            kind="hook",
            outcome=Outcome.error,
            reason=f"hook exited {exit_code} (non-blocking): {_short(stderr)}",
            detail=detail,
        )

    text = stdout.strip()
    if not text:
        return GuardDecision(guard=guard, kind="hook", outcome=Outcome.no_opinion, detail=detail)
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return GuardDecision(
            guard=guard,
            kind="hook",
            outcome=Outcome.no_opinion,
            reason="stdout was not JSON; ignored",
            detail=detail,
        )
    if not isinstance(document, dict):
        return GuardDecision(guard=guard, kind="hook", outcome=Outcome.no_opinion, detail=detail)

    specific = document.get("hookSpecificOutput")
    decision = None
    reason = None
    if isinstance(specific, dict):
        decision = specific.get("permissionDecision")
        reason = specific.get("permissionDecisionReason")
    if decision is None:
        legacy = document.get("decision")
        decision = {"block": "deny", "approve": "allow"}.get(str(legacy)) if legacy else None
        reason = reason or document.get("reason")
    if decision is None and document.get("continue") is False:
        decision = "deny"
        reason = reason or document.get("stopReason")

    outcome = {
        "deny": Outcome.block,
        "ask": Outcome.ask,
        "allow": Outcome.allow,
    }.get(str(decision), Outcome.no_opinion)
    return GuardDecision(
        guard=guard,
        kind="hook",
        outcome=outcome,
        reason=_short(str(reason)) if reason else None,
        detail=detail,
    )


async def run_hook(
    hook: HookSpec,
    payload: Payload,
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,  # noqa: ASYNC109 - a per-call budget, enforced with wait_for below
) -> GuardDecision:
    """Pipe one payload to one hook and interpret the result. Never raises for hook failures."""
    guard = f"hook:{hook.command}"
    effective = min(timeout, hook.timeout) if hook.timeout else timeout
    document = json.dumps(hook_input(payload, cwd)).encode()
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
    except OSError as exc:
        return GuardDecision(
            guard=guard,
            kind="hook",
            outcome=Outcome.error,
            reason=f"could not start hook: {exc}",
        )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(document), timeout=effective)
    except TimeoutError:
        process.kill()
        await process.wait()
        return GuardDecision(
            guard=guard,
            kind="hook",
            outcome=Outcome.error,
            reason=f"hook timed out after {effective:g}s",
            detail={"timeout": True},
        )
    elapsed_ms = (time.monotonic() - started) * 1000
    return parse_hook_output(
        guard,
        process.returncode if process.returncode is not None else -1,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        elapsed_ms,
    )
