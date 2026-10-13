"""Real-mode orchestration, driven by a scripted agent. No CLI is run and nothing costs money."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from tests.modules.compaction_survival.fake_agent import FakeAgent

from jev_harness.core.errors import BudgetExceededError, ConfigError
from jev_harness.core.runner import Budget
from jev_harness.modules.compaction_survival import real, service
from jev_harness.modules.compaction_survival.canary import make_plan
from jev_harness.modules.compaction_survival.service import CompactionOptions
from jev_harness.modules.compaction_survival.sources import discover_claude_code


def runners(agent: FakeAgent) -> real.RealRunners:
    return real.RealRunners(load=agent, probe=agent, action=agent)  # type: ignore[arg-type]


def options(project: Path, **kwargs: object) -> CompactionOptions:
    base: dict[str, object] = {
        "project": project,
        "mode": "real",
        "rounds": 2,
        "trials": 2,
        "max_runs": 500,
    }
    base.update(kwargs)
    return CompactionOptions(**base)  # type: ignore[arg-type]


def snapshot(project: Path) -> dict[str, str]:
    return {
        str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(project.rglob("*"))
        if p.is_file()
    }


async def test_everything_survives_and_nothing_is_flagged(claude_project: Path) -> None:
    """Must-not-flag: an agent that keeps every source produces no findings."""
    agent = FakeAgent()
    before = snapshot(claude_project)
    report = await service.execute_real(options(claude_project), runners(agent))
    assert report.findings == []
    assert snapshot(claude_project) == before  # the real project was never touched
    assert report.meta["mode"] == "real"
    assert report.meta["agent_version"] == "fake-1.0"
    assert report.metrics["survival.real-agent.root"].startswith("r0: 2/2 (100%)")
    assert "Simulated" not in report.render("md")


async def test_protocol_uses_a_fresh_session_per_point_and_the_exact_planned_call_count(
    claude_project: Path,
) -> None:
    agent = FakeAgent()
    opts = options(claude_project)
    await service.execute_real(opts, runners(agent))
    discovery = discover_claude_code(claude_project)
    behavior = len(make_plan(discovery, seed=opts.seed).behavior())
    assert len(agent.calls) == real.planned_calls(opts.rounds, opts.trials, behavior)
    assert agent.started == opts.trials * (opts.rounds + 1)  # one new session per point
    recalls = [c for c in agent.calls if c["prompt"] == real.RECALL_PROMPT]
    assert len(recalls) == agent.started  # every point is probed exactly once


async def test_losses_are_reported_for_the_sources_that_actually_dropped(
    claude_project: Path,
) -> None:
    def policy(path: str, compactions: int) -> bool:
        return path in {"CLAUDE.md", ".claude/rules/style.md"}  # scoped, nested, import dropped

    report = await service.execute_real(
        options(claude_project, trials=3), runners(FakeAgent(policy=policy))
    )
    by_rule: dict[str, set[str]] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule_id, set()).add((finding.location or "").split("/")[1])
    assert by_rule["compaction.scoped_rule_lost"] == {"scoped-py", "nested-sub"}
    assert by_rule["compaction.source_lost"] == {"import-extra"}
    assert "compaction.behavior_regression" in by_rule  # the import carried a behavior rule
    assert "root" not in set().union(*by_rule.values())


async def test_scoped_rules_not_loaded_at_baseline_are_a_loading_finding(
    claude_project: Path,
) -> None:
    # Break the trigger: the agent never reads src/jev/probe.py, so the scoped rule never loads.
    class NoTriggers(FakeAgent):
        def _load(self, workspace: Path, prompt: str):
            return super()._load(workspace, prompt.split(":")[0])

    report = await service.execute_real(options(claude_project, trials=3), runners(NoTriggers()))
    not_loaded = {
        f.location for f in report.findings if f.rule_id == "compaction.source_not_loaded"
    }
    assert not_loaded == {"real-agent/scoped-py", "real-agent/nested-sub"}
    assert not [f for f in report.findings if f.rule_id == "compaction.scoped_rule_lost"]


async def test_an_unconfirmed_compaction_stops_the_run_instead_of_scoring_survival(
    claude_project: Path,
) -> None:
    agent = FakeAgent(compact_works=False)
    with pytest.raises(real.CompactionNotConfirmedError, match="did not make the agent compact"):
        await service.execute_real(options(claude_project), runners(agent))
    # It stopped at the first point that needed a compaction rather than spending the rest.
    assert agent.started == 2


async def test_budget_caps_stop_the_run(claude_project: Path) -> None:
    opts = options(claude_project, max_runs=3)
    with pytest.raises(ConfigError, match=r"needs .* agent calls but --max-runs is 3"):
        await service.execute_real(opts, runners(FakeAgent()))

    discovery = discover_claude_code(claude_project)
    plan = make_plan(discovery, seed=0)
    with pytest.raises(BudgetExceededError, match="run cap reached"):
        await real.run_real(
            discovery, plan, runners(FakeAgent()), rounds=2, trials=2, budget=Budget(max_runs=4)
        )


async def test_real_mode_refuses_to_run_without_any_cap(claude_project: Path) -> None:
    opts = options(claude_project, max_runs=None, max_cost_usd=None)
    with pytest.raises(ConfigError, match="--max-cost USD and/or --max-runs N"):
        await service.execute_real(opts, runners(FakeAgent()))


async def test_codex_real_mode_is_refused_with_the_reason() -> None:
    with pytest.raises(ConfigError, match="claude-code only in v1"):
        service.build_real_runners(CompactionOptions(project=Path(), agent="codex", mode="real"))


async def test_behavior_probe_reads_the_file_the_agent_wrote(claude_project: Path) -> None:
    report = await service.execute_real(
        options(claude_project), runners(FakeAgent(follows_rules=False))
    )
    # Recall is fine but the agent stopped following the rule even at baseline: nothing to compare.
    assert report.metrics["behavior.real-agent.root"].startswith("r0: 0/2")
    assert not [f for f in report.findings if f.rule_id == "compaction.behavior_regression"]


def test_planned_calls_formula() -> None:
    # per trial: sum over r of (load + r compactions + recall + b behavior probes)
    assert real.planned_calls(rounds=1, trials=1, behavior_sources=0) == (1 + 0 + 1) + (1 + 1 + 1)
    assert real.planned_calls(rounds=3, trials=5, behavior_sources=2) == 5 * (
        sum(1 + r + 1 + 2 for r in range(4))
    )
