"""Check behaviour on hand-built survival data with known answers."""

from __future__ import annotations

from jev_harness.core.check import Severity
from jev_harness.modules.compaction_survival import checks
from jev_harness.modules.compaction_survival.results import Observation, StrategyRun
from jev_harness.modules.compaction_survival.sources import Source


def source(source_id: str, kind: str, path: str = "x.md") -> Source:
    return Source(id=source_id, kind=kind, path=path, loads=f"{kind} loads")  # type: ignore[arg-type]


def make_run(
    cells: dict[str, list[tuple[int, int]]],
    *,
    deterministic: bool = False,
    behavior: dict[str, list[tuple[int, int]]] | None = None,
) -> StrategyRun:
    """``cells[source] = [(recalled, n) per round]`` expanded into observations."""
    rounds = len(next(iter(cells.values()))) - 1
    run = StrategyRun(name="s", mode="real", deterministic=deterministic, rounds=rounds, trials=5)
    for source_id, per_round in cells.items():
        for round_, (recalled, n) in enumerate(per_round):
            beh = (behavior or {}).get(source_id)
            for trial in range(n):
                run.observations.append(
                    Observation(
                        source_id=source_id,
                        round=round_,
                        trial=trial,
                        recalled=trial < recalled,
                        behavior_followed=(trial < beh[round_][0]) if beh else None,
                    )
                )
    return run


def subject(run: StrategyRun, **kinds: str) -> checks.Subject:
    return checks.Subject(run, {sid: source(sid, kind, f"{sid}.md") for sid, kind in kinds.items()})


def test_source_lost_needs_credible_evidence_not_one_bad_trial() -> None:
    lost = make_run({"a": [(5, 5), (0, 5)], "b": [(5, 5), (4, 5)], "c": [(5, 5), (2, 5)]})
    findings = checks.SourceLost().run(subject(lost, a="root", b="root", c="rule"))
    assert sorted(f.location for f in findings) == ["s/a", "s/c"]  # 0/5 and 2/5; 4/5 is not enough
    assert "Suggested fix" in findings[0].message
    assert all(f.severity is Severity.flag for f in findings)


def test_a_single_trial_cannot_condemn_a_stochastic_source() -> None:
    run = make_run({"a": [(1, 1), (0, 1)]})
    assert checks.SourceLost().run(subject(run, a="root")) == []
    deterministic = make_run({"a": [(1, 1), (0, 1)]}, deterministic=True)
    (finding,) = checks.SourceLost().run(subject(deterministic, a="root"))
    assert "not recalled" in finding.message  # no interval is quoted for a deterministic strategy
    assert "95% CI" not in finding.message


def test_stable_survival_is_not_flagged_by_any_check() -> None:
    run = make_run({"a": [(5, 5)] * 4, "b": [(5, 5)] * 4}, behavior={"a": [(5, 5)] * 4})
    sub = subject(run, a="root", b="scoped_rule")
    for check in checks.default_checks():
        assert check.run(sub) == [], check.rule_id


def test_scoped_and_nested_loss_uses_its_own_rule_and_explains_the_documentation() -> None:
    run = make_run({"p": [(5, 5), (0, 5)], "n": [(5, 5), (0, 5)], "r": [(5, 5), (0, 5)]})
    sub = subject(run, p="scoped_rule", n="nested", r="root")
    scoped = checks.ScopedRuleLost().run(sub)
    assert sorted(f.location for f in scoped) == ["s/n", "s/p"]
    assert "documented behaviour" in scoped[0].message
    assert [f.location for f in checks.SourceLost().run(sub)] == ["s/r"]


def test_source_never_loaded_is_a_loading_problem_not_a_survival_one() -> None:
    run = make_run({"a": [(0, 5), (0, 5)]})
    sub = subject(run, a="scoped_rule")
    (finding,) = checks.SourceNotLoaded().run(sub)
    assert "before any compaction" in finding.message
    assert checks.SourceLost().run(sub) == []
    assert checks.ScopedRuleLost().run(sub) == []


def test_progressive_decay_needs_a_significant_stepwise_fall_that_is_not_yet_a_loss() -> None:
    decaying = make_run({"a": [(100, 100), (90, 100), (80, 100), (70, 100)]})
    (finding,) = checks.ProgressiveDecay(floor=0.5).run(subject(decaying, a="rule"))
    assert "100% -> 90% -> 80% -> 70%" in finding.message
    # Falling into a loss is reported as a loss, not also as decay.
    collapsed = make_run({"a": [(100, 100), (60, 100), (30, 100), (0, 100)]})
    assert checks.ProgressiveDecay().run(subject(collapsed, a="rule")) == []
    # One drop is a step, not a decay; a noisy small sample is not significant.
    step = make_run({"a": [(10, 10), (10, 10), (5, 10)]})
    assert checks.ProgressiveDecay(floor=0.1).run(subject(step, a="rule")) == []
    small = make_run({"a": [(5, 5), (4, 5), (3, 5), (3, 5)]})
    assert checks.ProgressiveDecay(floor=0.1).run(subject(small, a="rule")) == []
    one_round = make_run({"a": [(5, 5), (1, 5)]})
    assert checks.ProgressiveDecay().run(subject(one_round, a="rule")) == []


def test_behavior_regression_is_escalated_only_when_it_was_followed_before() -> None:
    run = make_run(
        {"a": [(5, 5), (5, 5)], "b": [(5, 5), (5, 5)], "c": [(5, 5), (5, 5)]},
        behavior={"a": [(5, 5), (0, 5)], "b": [(0, 5), (0, 5)], "c": [(5, 5), (5, 5)]},
    )
    findings = checks.BehaviorRegression().run(subject(run, a="root", b="root", c="root"))
    assert [f.location for f in findings] == ["s/a"]
    assert findings[0].severity is Severity.escalate
    assert "recall" not in findings[0].message  # recall was fine; the rule stopped being followed


def test_finding_ids_are_stable_and_namespaced() -> None:
    ids = [c.rule_id for c in checks.default_checks()]
    assert ids == [
        "compaction.source_not_loaded",
        "compaction.source_lost",
        "compaction.scoped_rule_lost",
        "compaction.progressive_decay",
        "compaction.behavior_regression",
    ]
