"""The five compaction_survival checks. Scoring is exact codeword match and mechanical rules."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from jev_harness.core.check import Check, Finding, Severity
from jev_harness.core.stats import compare_rates, wilson
from jev_harness.modules.compaction_survival.results import StrategyRun
from jev_harness.modules.compaction_survival.sources import Source

MODULE = "compaction"
DEFAULT_FLOOR = 0.8
#: A stochastic cell with fewer trials than this is never called low: Wilson's upper bound for
#: 0 of 1 is already below the default floor, so one bad trial would otherwise condemn a source.
MIN_TRIALS = 3
LOCAL_KINDS = {"scoped_rule", "nested"}

FIXES: dict[str, str] = {
    "root": "shorten the root file and put the critical rules first, or re-inject a short "
    "pinned-rules file after compaction (a SessionStart hook with the compact source)",
    "rule": "move the rule into the root file, or keep it short and re-inject it after compaction",
    "local": "move the rule to the root CLAUDE.md, which is documented as re-injected",
    "import": "inline the imported rule into the root file, or re-inject it after compaction",
    "scoped_rule": "move the rule to the root file or to an unscoped .claude/rules file, or "
    "re-read a matching file after compaction so the scoped rule reloads",
    "nested": "move the rule to the root file, or re-read a file in that directory after "
    "compaction so the nested file reloads",
    "agents_root": "keep critical rules near the top and short, and re-state them after compaction",
    "agents_nested": "move the rule to the project-root AGENTS.md",
}


@dataclass(slots=True)
class Subject:
    run: StrategyRun
    sources: dict[str, Source]
    floor: float = DEFAULT_FLOOR

    def source(self, source_id: str) -> Source:
        return self.sources[source_id]


def _rate_text(k: int, n: int, deterministic: bool = False) -> str:
    """``k/n`` with its interval; a deterministic strategy has no sampling noise to quantify."""
    if not n:
        return "n/a"
    if deterministic:
        return "recalled" if k == n else ("not recalled" if k == 0 else f"{k}/{n}")
    return f"{k}/{n}; {wilson(k, n)}"


def _t(run: StrategyRun, cell: tuple[int, int]) -> str:
    return _rate_text(cell[0], cell[1], run.deterministic)


def is_low(run: StrategyRun, recalled: int, n: int, floor: float) -> bool:
    """Whether a cell is credibly below the floor.

    A stochastic cell is low only when even its interval's upper end is below the floor, so a
    handful of trials cannot condemn a source (fewer than ``MIN_TRIALS`` never can). A
    deterministic strategy has no sampling noise, so a single failure is certain.
    """
    if n == 0:
        return False
    if run.deterministic:
        return recalled / n < floor
    if n < MIN_TRIALS:
        return False
    return wilson(recalled, n).high < floor


def _per_round(run: StrategyRun, source_id: str) -> dict[str, str]:
    return {
        f"round_{r}": _t(run, run.cell(source_id, r))
        for r in range(run.rounds + 1)
        if run.cell(source_id, r)[1]
    }


def _location(run: StrategyRun, source_id: str) -> str:
    return f"{run.name}/{source_id}"


class _SurvivalCheck(Check):
    def __init__(self, floor: float = DEFAULT_FLOOR) -> None:
        self.floor = floor

    def baseline_ok(self, run: StrategyRun, source_id: str) -> bool:
        recalled, n = run.cell(source_id, 0)
        return n > 0 and not is_low(run, recalled, n, self.floor)


class SourceLost(_SurvivalCheck):
    """An always-loaded source was recalled at baseline but not after compaction."""

    rule_id = f"{MODULE}.source_lost"
    severity = Severity.flag

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        run = subject.run
        for source_id in run.source_ids():
            source = subject.source(source_id)
            if source.kind in LOCAL_KINDS or not self.baseline_ok(run, source_id):
                continue
            recalled, n = run.cell(source_id, run.rounds)
            if not is_low(run, recalled, n, self.floor):
                continue
            base_k, base_n = run.cell(source_id, 0)
            findings.append(
                self.finding(
                    f"{source.path} ({source.kind}) was recalled {_t(run, (base_k, base_n))} "
                    f"at baseline but {_t(run, (recalled, n))} after {run.rounds} "
                    f"compaction(s). Suggested fix: {FIXES[source.kind]}.",
                    location=_location(run, source_id),
                    evidence={
                        "source": source.path,
                        "kind": source.kind,
                        "mode": run.mode,
                        "rounds": _per_round(run, source_id),
                        "loads": source.loads,
                    },
                )
            )
        return findings


class ScopedRuleLost(_SurvivalCheck):
    """A path-scoped rule or nested file was lost: the documented on-demand case."""

    rule_id = f"{MODULE}.scoped_rule_lost"
    severity = Severity.flag

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        run = subject.run
        for source_id in run.source_ids():
            source = subject.source(source_id)
            if source.kind not in LOCAL_KINDS or not self.baseline_ok(run, source_id):
                continue
            recalled, n = run.cell(source_id, run.rounds)
            if not is_low(run, recalled, n, self.floor):
                continue
            findings.append(
                self.finding(
                    f"{source.path} ({source.kind}) was not recalled after {run.rounds} "
                    f"compaction(s): {_t(run, (recalled, n))}. {source.loads}, so this is the "
                    f"documented behaviour, but a rule that only loads on demand is not "
                    f"protected across compaction. Suggested fix: {FIXES[source.kind]}.",
                    location=_location(run, source_id),
                    evidence={
                        "source": source.path,
                        "kind": source.kind,
                        "mode": run.mode,
                        "rounds": _per_round(run, source_id),
                        "globs": source.globs,
                    },
                )
            )
        return findings


class ProgressiveDecay(_SurvivalCheck):
    """Recall falls round after round without (yet) collapsing."""

    rule_id = f"{MODULE}.progressive_decay"
    severity = Severity.flag

    def run(self, subject: Subject) -> list[Finding]:
        run = subject.run
        if run.rounds < 2:
            return []
        findings: list[Finding] = []
        for source_id in run.source_ids():
            cells = [run.cell(source_id, r) for r in range(run.rounds + 1)]
            if any(n == 0 for _, n in cells):
                continue
            rates = [k / n for k, n in cells]
            if not (rates[0] > rates[-1] and all(a >= b for a, b in itertools.pairwise(rates))):
                continue
            if sum(1 for a, b in itertools.pairwise(rates) if b < a) < 2:
                continue
            last_k, last_n = cells[-1]
            if is_low(run, last_k, last_n, self.floor):
                continue  # already reported as lost
            first_k, first_n = cells[0]
            comparison = compare_rates(first_k, first_n, last_k, last_n)
            if not run.deterministic and not comparison.significant:
                continue
            findings.append(
                self.finding(
                    f"{subject.source(source_id).path} decays across rounds: "
                    + " -> ".join(f"{rate:.0%}" for rate in rates),
                    location=_location(run, source_id),
                    evidence={
                        "mode": run.mode,
                        "rounds": _per_round(run, source_id),
                        "p_value": comparison.p_value,
                    },
                )
            )
        return findings


class BehaviorRegression(_SurvivalCheck):
    """A rule the agent followed at baseline stops being followed after compaction."""

    rule_id = f"{MODULE}.behavior_regression"
    severity = Severity.escalate

    def run(self, subject: Subject) -> list[Finding]:
        run = subject.run
        findings: list[Finding] = []
        for source_id in run.behavior_ids():
            baseline = run.behavior_cell(source_id, 0)
            if baseline is None or is_low(run, *baseline, self.floor) or baseline[0] == 0:
                continue
            final = run.behavior_cell(source_id, run.rounds)
            if final is None or not is_low(run, *final, self.floor):
                continue
            source = subject.source(source_id)
            findings.append(
                self.finding(
                    f"the rule in {source.path} was followed {_t(run, baseline)} at baseline "
                    f"but {_t(run, final)} after {run.rounds} compaction(s). Suggested "
                    f"fix: {FIXES[source.kind]}.",
                    location=_location(run, source_id),
                    evidence={
                        "mode": run.mode,
                        "baseline": _t(run, baseline),
                        "final": _t(run, final),
                    },
                )
            )
        return findings


class SourceNotLoaded(_SurvivalCheck):
    """The source was not recalled even before compaction: a loading problem, not a survival one."""

    rule_id = f"{MODULE}.source_not_loaded"
    severity = Severity.flag

    def run(self, subject: Subject) -> list[Finding]:
        run = subject.run
        findings: list[Finding] = []
        for source_id in run.source_ids():
            recalled, n = run.cell(source_id, 0)
            if not is_low(run, recalled, n, self.floor):
                continue
            source = subject.source(source_id)
            findings.append(
                self.finding(
                    f"{source.path} ({source.kind}) was recalled only {_t(run, (recalled, n))} "
                    "before any compaction, so its survival cannot be judged. "
                    + (
                        f"The harness read {source.trigger_path} to load it."
                        if source.trigger_path
                        else source.loads + "."
                    ),
                    location=_location(run, source_id),
                    evidence={"source": source.path, "kind": source.kind, "mode": run.mode},
                )
            )
        return findings


def default_checks(floor: float = DEFAULT_FLOOR) -> list[Check]:
    return [
        SourceNotLoaded(floor),
        SourceLost(floor),
        ScopedRuleLost(floor),
        ProgressiveDecay(floor),
        BehaviorRegression(floor),
    ]
