"""Compose discovery, execution, checks, and the report for ``guard run``."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_harness.core.check import Eval, Finding
from jev_harness.core.report import Report
from jev_harness.core.stats import wilson
from jev_harness.modules.guard_bench import checks, matcher
from jev_harness.modules.guard_bench.bench import (
    DEFAULT_CONCURRENCY,
    DEFAULT_HOOK_TIMEOUT,
    BenchResult,
    run_bench,
)
from jev_harness.modules.guard_bench.corpus import Corpus, load_corpus
from jev_harness.modules.guard_bench.discovery import GuardConfig, discover


@dataclass(slots=True)
class GuardOptions:
    project: Path
    corpus_files: list[Path] = field(default_factory=list)
    include_default_corpus: bool = True
    generic_guards: list[str] = field(default_factory=list)
    home: Path | None = None
    include_managed: bool = True
    hook_timeout: float = DEFAULT_HOOK_TIMEOUT
    pass_env: list[str] = field(default_factory=list)
    concurrency: int = DEFAULT_CONCURRENCY
    baseline: Report | None = None
    class_floor: float = checks.DEFAULT_CLASS_FLOOR
    min_samples: int = checks.DEFAULT_MIN_SAMPLES
    agent_version: str | None = None


class GuardEval(Eval):
    module = checks.MODULE


@dataclass(slots=True)
class Plan:
    """What a run would do, computed without executing anything."""

    config: GuardConfig
    corpus: Corpus
    payload_total: int
    by_category: dict[str, int]
    by_disguise: dict[str, int]
    hooks_to_run: list[str]

    def lines(self) -> list[str]:
        out = [
            f"project:        {self.config.project}",
            f"settings files: {len(self.config.settings_files)}",
            f"permission rules: {len(self.config.rules)} (emulated matcher, an approximation)",
            f"hooks to run:   {len(self.config.hooks)}",
            f"payloads:       {self.payload_total} (strings only; none is executed)",
        ]
        out.extend(f"  hook: {command}" for command in self.hooks_to_run)
        out.append("by category:")
        out.extend(f"  {name}: {count}" for name, count in sorted(self.by_category.items()))
        out.append("by disguise:")
        out.extend(f"  {name}: {count}" for name, count in sorted(self.by_disguise.items()))
        out.extend(f"warning: {w}" for w in self.config.warnings)
        out.append("dry run: nothing was executed.")
        return out


def _load(options: GuardOptions) -> tuple[GuardConfig, Corpus]:
    config = discover(
        options.project,
        home=options.home,
        include_managed=options.include_managed,
        generic_guards=options.generic_guards,
    )
    corpus = load_corpus(options.corpus_files, include_default=options.include_default_corpus)
    return config, corpus


def plan(options: GuardOptions) -> Plan:
    config, corpus = _load(options)
    payloads = corpus.expand(config.project)
    return Plan(
        config=config,
        corpus=corpus,
        payload_total=len(payloads),
        by_category=dict(Counter(p.category for p in payloads)),
        by_disguise=dict(Counter(p.disguise for p in payloads)),
        hooks_to_run=[h.command for h in config.hooks],
    )


def _rate_text(k: int, n: int, noun: str) -> str:
    return f"{k}/{n} {noun}; {wilson(k, n)}" if n else "n/a (no payloads)"


def build_metrics(bench: BenchResult, floor_check: checks.DisguiseClassFloor) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    blocking = [r for r in bench.results if r.payload.expected == "block"]
    benign = [r for r in bench.results if r.payload.expected == "allow"]
    metrics["payloads_total"] = len(bench.results)
    metrics["payloads_expected_block"] = len(blocking)
    metrics["payloads_benign"] = len(benign)
    metrics["guarded_overall"] = _rate_text(
        sum(1 for r in blocking if not r.got_through), len(blocking), "guarded"
    )

    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for result in blocking:
        per_category[result.payload.category][1] += 1
        if not result.got_through:
            per_category[result.payload.category][0] += 1
    for name, (k, n) in sorted(per_category.items()):
        metrics[f"guarded.category.{name}"] = _rate_text(k, n, "guarded")

    for name, (k, n) in floor_check.class_rates(bench).items():
        metrics[f"guarded.disguise.{name}"] = _rate_text(k, n, "guarded")

    metrics["benign_blocked"] = _rate_text(
        sum(1 for r in benign if r.verdict == "stopped"), len(benign), "blocked"
    )
    metrics["benign_prompted"] = sum(1 for r in benign if r.verdict == "gated")
    metrics["hook_runs"] = bench.hook_runs
    metrics["hook_errors"] = bench.hook_errors
    return metrics


def build_report(bench: BenchResult, options: GuardOptions) -> Report:
    config = bench.config
    all_checks = checks.default_checks(
        baseline=options.baseline, class_floor=options.class_floor, min_samples=options.min_samples
    )
    evaluator = GuardEval(all_checks)
    findings: list[Finding] = evaluator.run_checks(bench)
    floor_check = next(c for c in all_checks if isinstance(c, checks.DisguiseClassFloor))

    escalations = sum(1 for f in findings if f.severity.value == "escalate")
    blocking = [r for r in bench.results if r.payload.expected == "block"]
    through = sum(1 for r in blocking if r.got_through)
    if not config.has_guards:
        summary = (
            f"No guards were found, so all {through} of {len(blocking)} dangerous payloads "
            "reached the agent unblocked."
        )
    else:
        summary = (
            f"{through} of {len(blocking)} dangerous payloads got through the "
            f"{len(config.hooks)} hook(s) and {len(config.rules)} permission rule(s) tested; "
            f"{escalations} intent(s) need attention."
        )

    limitations = [
        "The corpus is a fixed, versioned set of disguises. A clean result means these "
        "payloads were stopped, not that the setup is safe.",
        "Payloads are only sent to guards as JSON on stdin; nothing is run. A hook script you "
        "wrote can still have side effects of its own when it receives them.",
        "Hooks ran in a temporary working directory with a temporary HOME and a reduced "
        "environment (see --pass-env), which can change a hook that depends on either.",
        "An `ask` outcome counts as guarded because a person is prompted; hook timeouts and "
        "crashes count as the payload getting through, because the agent proceeds.",
    ]
    if config.rules:
        limitations.extend(matcher.ASSUMPTIONS)
    if bench.hook_errors:
        limitations.append(
            f"{bench.hook_errors} of {bench.hook_runs} hook runs failed or timed out; "
            "see evidence reasons and raise --hook-timeout if that is not intended."
        )
    limitations.extend(config.warnings)

    meta: dict[str, Any] = {
        "agent_version": bench.agent_version or "unknown",
        "config_sha": bench.config_sha,
        "corpus_sha": bench.corpus_sha,
        "matcher": "emulated" if config.rules else "none",
        "project": config.project,
        "hooks": len(config.hooks),
        "permission_rules": len(config.rules),
    }
    return evaluator.build_report(
        title="guard_bench: hooks and permission rules",
        summary=summary,
        findings=findings,
        metrics=build_metrics(bench, floor_check),
        meta=meta,
        limitations=limitations,
    )


async def execute(options: GuardOptions) -> Report:
    config, corpus = _load(options)
    bench = await run_bench(
        config,
        corpus,
        home=options.home,
        hook_timeout=options.hook_timeout,
        pass_env=options.pass_env,
        concurrency=options.concurrency,
        agent_version=options.agent_version,
    )
    return build_report(bench, options)
