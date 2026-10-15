"""Compose discovery, planting, runs, checks, and the report for ``compaction run``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_harness.core.check import Eval, Finding
from jev_harness.core.errors import ConfigError
from jev_harness.core.report import Report
from jev_harness.core.runner import AgentRunner, Budget, ClaudeCodeRunner
from jev_harness.modules.compaction_survival import checks, real, sim
from jev_harness.modules.compaction_survival.canary import (
    CanaryPosition,
    Plan,
    canary_text,
    insert_block,
    make_plan,
)
from jev_harness.modules.compaction_survival.results import StrategyRun
from jev_harness.modules.compaction_survival.sources import Discovery, discover

DEFAULT_STRATEGIES = ("keep-recent", "claude-code-documented")
PROBE_DISALLOWED_TOOLS = ("Read", "Grep", "Glob", "Bash", "Edit", "Write")


class CompactionEval(Eval):
    module = checks.MODULE


@dataclass(slots=True)
class CompactionOptions:
    project: Path
    agent: str = "claude-code"
    mode: str = "simulated"
    rounds: int = 3
    trials: int = 5
    strategies: list[str] = field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    custom_summarizer: str | None = None
    seed: int = 0
    filler_turns: int = 40
    position: CanaryPosition = "end"
    behavior_sources: int = 2
    floor: float = checks.DEFAULT_FLOOR
    trigger_prompt: str = real.DEFAULT_TRIGGER_PROMPT
    max_cost_usd: float | None = None
    max_runs: int | None = None
    model: str | None = None
    timeout_seconds: float = 600.0
    permission_mode: str = "acceptEdits"


def validate(options: CompactionOptions) -> None:
    if options.rounds < 1:
        raise ConfigError("--rounds must be at least 1")
    if options.trials < 1:
        raise ConfigError("--trials must be at least 1")
    if options.mode not in {"simulated", "real"}:
        raise ConfigError("--mode must be simulated or real")
    if options.position not in {"start", "end"}:
        raise ConfigError("--canary-position must be start or end")
    if not 0.0 < options.floor <= 1.0:
        raise ConfigError("--floor must be within (0, 1]")


def prepare(options: CompactionOptions) -> tuple[Discovery, Plan]:
    validate(options)
    discovery = discover(options.agent, options.project)
    if not discovery.sources:
        raise ConfigError(
            "no instruction sources found to test: "
            + ("; ".join(discovery.warnings) or "add a CLAUDE.md or AGENTS.md")
        )
    return discovery, make_plan(
        discovery, seed=options.seed, behavior_sources=options.behavior_sources
    )


def plan_lines(options: CompactionOptions) -> list[str]:
    """Everything a run would do, without doing any of it."""
    discovery, plan = prepare(options)
    lines = [
        f"agent:       {discovery.agent}",
        f"project:     {discovery.project}",
        f"mode:        {options.mode}",
        f"rounds:      {options.rounds}   trials: {options.trials}",
        "sources to probe:",
    ]
    for canary in plan.canaries:
        extras = " + behavior rule" if canary.header else ""
        lines.append(
            f"  {canary.source.id:<28} {canary.source.kind:<12} {canary.source.path}{extras}"
        )
    lines.extend(f"not probed: {note}" for note in discovery.not_probed)
    lines.extend(f"warning: {w}" for w in discovery.warnings)
    if options.mode == "real":
        calls = real.planned_calls(options.rounds, options.trials, len(plan.behavior()))
        lines.append(
            f"agent calls: {calls} ({options.trials} trial(s) x {options.rounds + 1} measurement "
            "points, each in a fresh session)"
        )
        lines.append(
            "cost: unknown until run; the agent reports total_cost_usd per call and --max-cost / "
            "--max-runs stop the run"
        )
        lines.append(f"compaction trigger prompt: {options.trigger_prompt!r} (confirmed by a hook)")
    else:
        lines.append(
            "strategies:  "
            + ", ".join(
                [
                    *options.strategies,
                    *([options.custom_summarizer] if options.custom_summarizer else []),
                ]
            )
        )
        lines.append("no agent is called in simulated mode and nothing costs money.")
    lines.append("dry run: nothing was executed and no file was written.")
    return lines


def _source_texts(discovery: Discovery, plan: Plan, position: CanaryPosition) -> dict[str, str]:
    root = Path(discovery.project)
    texts: dict[str, str] = {}
    for canary in plan.canaries:
        original = (root / canary.source.path).read_text(encoding="utf-8", errors="replace")
        texts[canary.source.id] = insert_block(original, canary_text(canary), position)
    return texts


def run_simulated(options: CompactionOptions) -> tuple[Discovery, Plan, list[StrategyRun]]:
    discovery, plan = prepare(options)
    texts = _source_texts(discovery, plan, options.position)
    runs = [
        sim.run_strategy(
            strategy,
            plan,
            rounds=options.rounds,
            trials=options.trials,
            filler_turns=options.filler_turns,
            seed=options.seed,
            texts=texts,
        )
        for strategy in sim.resolve_strategies(options.strategies, options.custom_summarizer)
    ]
    return discovery, plan, runs


def build_real_runners(options: CompactionOptions) -> real.RealRunners:
    if options.agent not in {"claude-code", "claude"}:
        raise ConfigError(
            "real mode supports claude-code only in v1: Codex has no verified way to confirm a "
            "compaction happened. Use --mode simulated for Codex sources."
        )
    common: dict[str, Any] = {
        "model": options.model,
        "timeout_seconds": options.timeout_seconds,
    }
    return real.RealRunners(
        load=ClaudeCodeRunner(permission_mode=options.permission_mode, **common),
        probe=ClaudeCodeRunner(
            max_turns=1,
            extra_args=["--disallowedTools", ",".join(PROBE_DISALLOWED_TOOLS)],
            **common,
        ),
        action=ClaudeCodeRunner(permission_mode=options.permission_mode, **common),
    )


async def run_real_mode(
    options: CompactionOptions,
    runners: real.RealRunners | None = None,
) -> tuple[Discovery, Plan, list[StrategyRun], Budget, str | None]:
    discovery, plan = prepare(options)
    if options.max_cost_usd is None and options.max_runs is None:
        raise ConfigError(
            "real mode calls a paid agent: pass --max-cost USD and/or --max-runs N so the run has "
            "a cap (run with --dry-run to see how many calls it needs)"
        )
    runners = runners or build_real_runners(options)
    if not runners.load.available():
        raise ConfigError("the claude executable was not found on PATH; real mode needs it")
    budget = Budget(max_cost_usd=options.max_cost_usd, max_runs=options.max_runs)
    needed = real.planned_calls(options.rounds, options.trials, len(plan.behavior()))
    if options.max_runs is not None and needed > options.max_runs:
        raise ConfigError(
            f"this run needs {needed} agent calls but --max-runs is {options.max_runs}; "
            "raise it or reduce --rounds/--trials"
        )
    version = runners.load.version()
    run = await real.run_real(
        discovery,
        plan,
        runners,
        rounds=options.rounds,
        trials=options.trials,
        budget=budget,
        trigger_prompt=options.trigger_prompt,
        position=options.position,
    )
    return discovery, plan, [run], budget, version


def _matrix_line(run: StrategyRun, source_id: str) -> str:
    parts = []
    for round_ in range(run.rounds + 1):
        recalled, n = run.cell(source_id, round_)
        if n:
            parts.append(f"r{round_}: {recalled}/{n} ({recalled / n:.0%})")
    return " | ".join(parts)


FIX_HEADLINE = (
    "A source that fails here is only a candidate for a fix: re-run after changing the config."
)


def build_report(
    options: CompactionOptions,
    discovery: Discovery,
    plan: Plan,
    runs: list[StrategyRun],
    *,
    agent_version: str | None = None,
    budget: Budget | None = None,
) -> Report:
    sources = {c.source.id: c.source for c in plan.canaries}
    all_checks = checks.default_checks(options.floor)
    evaluator = CompactionEval(all_checks)
    findings: list[Finding] = []
    for run in runs:
        findings.extend(evaluator.run_checks(checks.Subject(run, sources, options.floor)))

    metrics: dict[str, Any] = {"sources_probed": len(plan.canaries)}
    for run in runs:
        for source_id in run.source_ids():
            metrics[f"survival.{run.name}.{source_id}"] = _matrix_line(run, source_id)
        final_ok = sum(
            1
            for source_id in run.source_ids()
            if run.cell(source_id, run.rounds)[1]
            and run.cell(source_id, run.rounds)[0] == run.cell(source_id, run.rounds)[1]
        )
        metrics[f"strategy.{run.name}.survive_all_trials_after_{run.rounds}_rounds"] = (
            f"{final_ok}/{len(run.source_ids())} sources"
        )
        for source_id in run.behavior_ids():
            line = []
            for round_ in range(run.rounds + 1):
                cell = run.behavior_cell(source_id, round_)
                if cell:
                    line.append(f"r{round_}: {cell[0]}/{cell[1]}")
            metrics[f"behavior.{run.name}.{source_id}"] = " | ".join(line)
        if run.invalid_points:
            metrics[f"discarded_points.{run.name}"] = run.invalid_points
    if budget is not None:
        metrics["budget"] = budget.summary()

    simulated = options.mode == "simulated"
    limitations = [
        "Recall is scored by exact match on a planted codeword, and behavior by the first line "
        "of a file the agent creates. A source that survives compaction is not thereby followed "
        "well in general.",
        "Canaries are planted in a disposable copy of the project; your files were not changed.",
        *discovery.warnings,
        *[f"Not probed: {note}." for note in discovery.not_probed],
        "User-level instruction files (for example ~/.claude/CLAUDE.md) are out of scope.",
    ]
    if simulated:
        limitations.append(
            "Simulated mode applies a summarizer to a synthetic session. It measures the "
            "summarizer and the pipeline, not any agent, and its numbers must not be quoted as "
            "agent behaviour. The claude-code-documented strategy is a model of the "
            "documentation, not a measurement."
        )
    for run in runs:
        limitations.extend(f"{run.name}: {note}" for note in run.notes)
        if run.mode == "real" and run.trials < 30:
            limitations.append(
                f"{run.name}: {run.trials} trial(s) per cell; the intervals are wide and a rate "
                "from so few trials is provisional."
            )
    if not simulated:
        limitations.append(
            "Real mode relies on an unverified compaction trigger prompt, confirmed per point by "
            "a PreCompact hook in the disposable copy; points without a confirmed compaction are "
            "discarded and counted, not scored."
        )

    lost = sum(
        1
        for f in findings
        if f.rule_id in {f"{checks.MODULE}.source_lost", f"{checks.MODULE}.scoped_rule_lost"}
    )
    summary = (
        f"{len(plan.canaries)} instruction source(s) probed over {options.rounds} compaction "
        f"round(s); {lost} did not survive."
        + (" Simulated: these numbers describe the summarizer, not an agent." if simulated else "")
    )
    return evaluator.build_report(
        title="compaction: what survives context compaction",
        summary=summary,
        findings=findings,
        metrics=metrics,
        meta={
            "mode": options.mode,
            "agent": discovery.agent,
            "agent_version": agent_version or "unknown",
            "rounds": options.rounds,
            "trials": ",".join(f"{r.name}={r.trials}" for r in runs),
            "strategies": ",".join(r.name for r in runs),
            "canary_position": options.position,
        },
        limitations=limitations,
    ).model_copy(
        update={"headline": ["sources_probed", "strategy.*", "survival.*", "behavior.*", "budget"]}
    )


def execute_simulated(options: CompactionOptions) -> Report:
    discovery, plan, runs = run_simulated(options)
    return build_report(options, discovery, plan, runs)


async def execute_real(
    options: CompactionOptions, runners: real.RealRunners | None = None
) -> Report:
    discovery, plan, runs, budget, version = await run_real_mode(options, runners)
    return build_report(options, discovery, plan, runs, agent_version=version, budget=budget)


__all__ = [
    "AgentRunner",
    "CompactionOptions",
    "build_report",
    "execute_real",
    "execute_simulated",
    "plan_lines",
]
