from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jev_harness.core.errors import ConfigError
from jev_harness.core.workspace import Workspace
from jev_harness.modules.compaction_survival import sim
from jev_harness.modules.compaction_survival.canary import insert_block, make_plan, plant
from jev_harness.modules.compaction_survival.sources import discover_claude_code


def digest(project: Path) -> dict[str, str]:
    return {
        str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(project.rglob("*"))
        if p.is_file()
    }


def test_plan_is_reproducible_unique_and_marks_behavior_sources(claude_project: Path) -> None:
    discovery = discover_claude_code(claude_project)
    one = make_plan(discovery, seed=7)
    two = make_plan(discovery, seed=7)
    other = make_plan(discovery, seed=8)
    assert [c.codeword for c in one.canaries] == [c.codeword for c in two.canaries]
    assert [c.codeword for c in one.canaries] != [c.codeword for c in other.canaries]
    words = [c.codeword for c in one.canaries]
    assert len(words) == len(set(words)) == 5
    assert [c.source.id for c in one.behavior()] == ["root", "import-extra"]
    assert make_plan(discovery, seed=0, behavior_sources=0).behavior() == []


def test_planting_touches_only_the_disposable_copy(claude_project: Path) -> None:
    before = digest(claude_project)
    plan = make_plan(discover_claude_code(claude_project), seed=1)
    with Workspace.create(claude_project) as workspace:
        plant(workspace, plan)
        root_text = workspace.read_file("CLAUDE.md")
        assert f"Codeword for root: {plan.by_id()['root'].codeword}" in root_text
        assert "first line must be exactly: # jev-header-root-" in root_text
        assert workspace.exists("sub/.jev-trigger.txt")
        assert workspace.exists("src/jev/probe.py")
        assert workspace.path.resolve() != claude_project.resolve()
    assert digest(claude_project) == before


def test_canary_position_start_goes_after_frontmatter() -> None:
    text = "---\npaths: x\n---\nBody rule.\n"
    assert insert_block(text, "BLOCK", "start") == "---\npaths: x\n---\nBLOCK\n\nBody rule.\n"
    assert insert_block("Body\n", "BLOCK", "start") == "BLOCK\n\nBody\n"
    assert insert_block("Body\n", "BLOCK", "end") == "Body\n\nBLOCK\n"


# ----------------------------------------------------------------- simulation


@pytest.fixture
def plan(claude_project: Path):
    return make_plan(discover_claude_code(claude_project), seed=3)


def survived(run: sim.StrategyRun, source_id: str) -> list[bool]:  # type: ignore[name-defined]
    return [bool(run.cell(source_id, r)[0]) for r in range(run.rounds + 1)]


def test_keep_recent_loses_everything_after_the_first_compaction(plan) -> None:
    run = sim.run_strategy(sim.keep_recent(10), plan, rounds=3, trials=5, filler_turns=40)
    for canary in plan.canaries:
        assert survived(run, canary.source.id) == [True, False, False, False]
        assert run.cell(canary.source.id, 0) == (1, 1)  # deterministic: one trial, not five
    assert run.trials == 1
    assert any("ran 1 trial instead of 5" in note for note in run.notes)
    root = run.behavior_cell("root", 0), run.behavior_cell("root", 1)
    assert root == ((1, 1), (0, 1))
    assert run.behavior_cell("rule-style", 0) is None  # no behavior probe on this source


def test_documented_strategy_keeps_reinjected_sources_and_loses_the_rest(plan) -> None:
    run = sim.run_strategy(sim.claude_code_documented(), plan, rounds=3, trials=1)
    assert survived(run, "root") == [True] * 4
    assert survived(run, "rule-style") == [True] * 4
    # Not re-injected per the docs (or the docs are silent): only the summary can carry them.
    for lost in ("scoped-py", "nested-sub", "import-extra"):
        assert survived(run, lost)[3] is False


def test_extractive_summary_depends_on_where_the_codeword_sits(plan) -> None:
    long_text = ("Explanatory prose. " * 20).strip()
    at = {}
    for position in ("start", "end"):
        texts = {
            c.source.id: insert_block(
                long_text, f"Codeword for {c.source.id}: {c.codeword}", position
            )  # type: ignore[arg-type]
            for c in plan.canaries
        }
        run = sim.run_strategy(sim.extractive(80), plan, rounds=2, trials=1, texts=texts)
        at[position] = survived(run, "root")
    assert at["start"] == [True, True, False]  # kept by the first summary, lost by summarising it
    assert at["end"] == [True, False, False]  # the summary only reads the first 80 characters


def test_custom_summarizer_is_loaded_run_with_all_trials_and_validated(plan) -> None:
    keep = sim.load_custom_summarizer(
        "tests.modules.compaction_survival.summarizers:keep_codewords"
    )
    run = sim.run_strategy(keep, plan, rounds=2, trials=4)
    assert run.trials == 4
    assert run.deterministic is False
    assert all(survived(run, c.source.id) == [True, True, True] for c in plan.canaries)

    drop = sim.load_custom_summarizer(
        "tests.modules.compaction_survival.summarizers:drop_everything"
    )
    assert survived(sim.run_strategy(drop, plan, rounds=1, trials=1), "root") == [True, False]

    bad = sim.load_custom_summarizer("tests.modules.compaction_survival.summarizers:not_a_string")
    with pytest.raises(ConfigError, match="must return a string"):
        sim.run_strategy(bad, plan, rounds=1, trials=1)


@pytest.mark.parametrize(
    "spec", ["nocolon", "no.such.module:f", "tests.modules.compaction_survival.summarizers:nope"]
)
def test_bad_summarizer_specs_fail_loudly(spec: str) -> None:
    with pytest.raises(ConfigError):
        sim.load_custom_summarizer(spec)


def test_resolve_strategies_validates_names() -> None:
    assert [s.name for s in sim.resolve_strategies(["keep-recent", "extractive"], None)] == [
        "keep-recent",
        "extractive",
    ]
    with pytest.raises(ConfigError, match="unknown strategy"):
        sim.resolve_strategies(["bogus"], None)
    with pytest.raises(ConfigError, match="no compaction strategy"):
        sim.resolve_strategies([], None)
