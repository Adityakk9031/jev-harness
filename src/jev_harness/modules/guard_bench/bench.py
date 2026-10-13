"""Run every payload through every guard and record what happened."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.workspace import Workspace
from jev_harness.modules.guard_bench.corpus import Corpus, Payload
from jev_harness.modules.guard_bench.decision import GuardDecision, Outcome
from jev_harness.modules.guard_bench.discovery import GuardConfig
from jev_harness.modules.guard_bench.hooks import build_env, hook_applies, run_hook
from jev_harness.modules.guard_bench.matcher import PermissionMatcher

Verdict = Literal["stopped", "gated", "allowed"]
DEFAULT_CONCURRENCY = 8
DEFAULT_HOOK_TIMEOUT = 10.0


class PayloadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: Payload
    verdict: Verdict
    decisions: list[GuardDecision] = Field(default_factory=list)
    #: The judge's advisory opinion (dangerous, safe, unclear, unavailable), if one was asked.
    #: It never changes ``verdict``.
    judge: str | None = None
    judge_confidence: float | None = None

    @property
    def got_through(self) -> bool:
        return self.verdict == "allowed"


class BenchResult(BaseModel):
    """Raw outcome of one bench run, before any check interprets it."""

    model_config = ConfigDict(extra="forbid")

    config: GuardConfig
    corpus_sha: str
    config_sha: str
    agent_version: str | None = None
    results: list[PayloadResult]
    hook_runs: int = 0
    hook_errors: int = 0


def verdict_of(decisions: list[GuardDecision]) -> Verdict:
    """Stopped if any guard blocked, gated if any asked, otherwise it got through."""
    if any(d.outcome is Outcome.block for d in decisions):
        return "stopped"
    if any(d.outcome is Outcome.ask for d in decisions):
        return "gated"
    return "allowed"


def _agent_version() -> str | None:
    try:
        from jev_harness.core.runner import ClaudeCodeRunner

        return ClaudeCodeRunner().version()
    except Exception:  # version lookup is best effort; its absence is reported, not fatal
        return None


ProgressFn = Callable[[int, int], Awaitable[None] | None]


async def run_bench(
    config: GuardConfig,
    corpus: Corpus,
    *,
    home: Path | None = None,
    hook_timeout: float = DEFAULT_HOOK_TIMEOUT,
    pass_env: list[str] | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    agent_version: str | None = None,
    isolate_home: bool = False,
) -> BenchResult:
    """Exercise the guards. Hooks run inside a disposable workspace with its own empty HOME."""
    project = Path(config.project)
    payloads = corpus.expand(str(project))
    matcher = PermissionMatcher(config.rules, project, home) if config.rules else None
    semaphore = asyncio.Semaphore(max(1, concurrency))
    counters = {"runs": 0, "errors": 0}

    with Workspace.create(name="guard-bench") as workspace:
        # The real HOME by default: hooks configured at user scope live under ~/.claude, and a
        # temporary HOME would make them "command not found", which reads as every payload
        # getting through. --isolate-home trades that for containment of hook side effects.
        hook_home = workspace.home if isolate_home else Path.home()
        env = build_env(hook_home, project, pass_env or [])

        async def evaluate(payload: Payload) -> PayloadResult:
            decisions: list[GuardDecision] = []
            if matcher is not None:
                decisions.append(matcher.decide(payload))
            for hook in config.hooks:
                if not hook_applies(hook.matcher, payload.tool):
                    continue
                async with semaphore:
                    decision = await run_hook(
                        hook, payload, cwd=workspace.path, env=env, timeout=hook_timeout
                    )
                counters["runs"] += 1
                if decision.outcome is Outcome.error:
                    counters["errors"] += 1
                decisions.append(decision)
            return PayloadResult(
                payload=payload, verdict=verdict_of(decisions), decisions=decisions
            )

        results = await asyncio.gather(*(evaluate(p) for p in payloads))

    return BenchResult(
        config=config,
        corpus_sha=corpus.sha(),
        config_sha=config.sha(),
        agent_version=agent_version if agent_version is not None else _agent_version(),
        results=list(results),
        hook_runs=counters["runs"],
        hook_errors=counters["errors"],
    )
