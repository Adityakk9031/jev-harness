"""The judge is advisory: it adds evidence about payloads, and never changes a verdict."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from tests.judges import ScriptedJudge, install_fake_jev
from tests.modules.guard_bench.conftest import MakeProject, build_project
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.errors import BudgetExceededError
from jev_harness.core.judge import JudgeQuestion
from jev_harness.core.judging import JudgeBudget
from jev_harness.core.report import Report
from jev_harness.modules.guard_bench import GuardOptions, execute
from jev_harness.modules.guard_bench.bench import BenchResult, PayloadResult, run_bench
from jev_harness.modules.guard_bench.corpus import load_corpus
from jev_harness.modules.guard_bench.discovery import discover
from jev_harness.modules.guard_bench.judging import select_for_judging

MARKERS = (
    "jev-canary", ".env", "exfil.invalid", ".claude/settings", "sudo", "id_rsa",
    "aws/credentials", "printenv", "push --force", "reset --hard", "clean -fdx",
)  # fmt: skip


def rule(question: JudgeQuestion) -> tuple[str, float] | None:
    """A judge that recognises obvious targets but cannot see through base64."""
    tool_input = question.state["input"]
    text = json.dumps([tool_input.get("command"), tool_input.get("file_path")])
    return ("dangerous", 0.9) if any(m in text for m in MARKERS) else ("safe", 0.9)


async def run(project: Path, judge: ScriptedJudge | None, **kwargs: Any) -> Report:
    options = GuardOptions(
        project=project, home=None, include_managed=False, agent_version="t", judge=judge,
        judge_budget=JudgeBudget(max_calls=5000), judge_sample=5000, **kwargs,
    )  # fmt: skip
    return await execute(options)


@pytest.fixture(scope="module")
def naive_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_project(tmp_path_factory.mktemp("j"), "naive", hooks=["naive_guard.py"])


async def test_the_judge_never_changes_what_counts_as_getting_through(naive_project: Path) -> None:
    plain = await run(naive_project, None)
    judged = await run(naive_project, ScriptedJudge(rule))
    assert judged.metrics["guarded_overall"] == plain.metrics["guarded_overall"]
    assert [(f.rule_id, f.location, f.severity) for f in judged.findings] == [
        (f.rule_id, f.location, f.severity) for f in plain.findings
    ]
    assert judged.meta["judge"] == "scripted"
    assert plain.meta["judge"] == "none"
    assert "judge_calls" not in plain.metrics


async def test_the_report_shows_what_a_model_guard_would_and_would_not_have_caught(
    naive_project: Path,
) -> None:
    judge = ScriptedJudge(rule)
    report = await run(naive_project, judge)

    # Encoded and interpreter-wrapped variants hide their markers or keep them: base64 hides them.
    encoded = report.metrics["judge_guard.would_catch.encoded"]
    assert encoded.startswith("0/")
    variable = report.metrics["judge_guard.would_catch.variable"]
    assert variable.split("/")[0] != "0"
    assert report.metrics["judge_guard.false_blocks_on_benign"].startswith("0/68")

    # Every judged bypass carries the opinion next to the exact variant string.
    finding = next(f for f in report.findings if f.location == "rm-recursive")
    by_id = {v["id"]: v for v in finding.evidence["variants"]}
    assert by_id["rm-recursive~variable-command"]["judge"] == "dangerous"
    assert by_id["rm-recursive~encoded-pipe"]["judge"] == "safe"
    assert "advisory" in "\n".join(report.limitations)
    # The judge saw the tool call as data and was told it would not be run.
    first = judge.questions[0]
    assert "will not be run" in first.question
    assert set(first.options) == {"dangerous", "safe", "unclear"}


async def test_a_failing_or_unsure_judge_yields_no_opinion_not_a_clean_bill(
    naive_project: Path,
) -> None:
    down = await run(naive_project, ScriptedJudge(lambda q: None))
    assert "unavailable" in down.metrics["judge_guard.no_usable_answer"]
    assert down.metrics["judge_guard.would_catch_bypasses"] == "n/a (no payloads)"
    finding = next(f for f in down.findings if f.location == "rm-recursive")
    assert {v["judge"] for v in finding.evidence["variants"]} == {"unavailable"}

    unsure = await run(naive_project, ScriptedJudge(lambda q: ("dangerous", 0.4)))
    assert {
        v["judge"]
        for v in next(f for f in unsure.findings if f.location == "rm-recursive").evidence[
            "variants"
        ]
    } == {"unclear"}
    assert unsure.metrics["judge_guard.would_catch_bypasses"] == "n/a (no payloads)"


async def test_a_judge_that_over_blocks_is_measured_on_the_benign_controls(
    naive_project: Path,
) -> None:
    report = await run(naive_project, ScriptedJudge(lambda q: ("dangerous", 0.95)))
    assert report.metrics["judge_guard.false_blocks_on_benign"].startswith("68/68")


async def test_the_call_cap_stops_the_run_before_any_call(naive_project: Path) -> None:
    judge = ScriptedJudge(rule)
    options = GuardOptions(
        project=naive_project, home=None, include_managed=False, judge=judge,
        judge_budget=JudgeBudget(max_calls=10), judge_sample=500,
    )  # fmt: skip
    with pytest.raises(BudgetExceededError, match="judge call"):
        await execute(options)
    assert judge.questions == []


async def test_selection_is_deterministic_spread_over_classes_and_skips_stopped_payloads(
    naive_project: Path,
) -> None:
    config = discover(naive_project, home=None, include_managed=False)
    bench: BenchResult = await run_bench(config, load_corpus(), agent_version="t")
    chosen = select_for_judging(bench, 40)
    assert len(chosen) == 40
    assert [r.payload.id for r in chosen] == [r.payload.id for r in select_for_judging(bench, 40)]
    assert len({r.payload.disguise for r in chosen}) >= 8
    # A quarter of a small sample goes to benign controls so their false-block rate has a sample.
    assert sum(r.payload.expected == "allow" for r in chosen) == 10
    for result in chosen:
        assert isinstance(result, PayloadResult)
        # Only dangerous payloads that got through, or benign ones that were not blocked.
        assert result.verdict != "stopped"
        assert result.got_through


# ---------------------------------------------------------------------- CLI

runner = CliRunner()


def invoke(project: Path, *args: str, key: bool = True):
    return runner.invoke(
        app,
        [
            "guard",
            "run",
            "--project",
            str(project),
            "--no-user-settings",
            "--no-managed-settings",
            *args,
        ],
        env={"JEV_API_KEY": "test-key" if key else ""},
    )


def test_cli_with_jev_records_the_judge_and_respects_the_sample(naive_project: Path) -> None:
    calls: list[dict[str, Any]] = []
    with respx.mock(assert_all_called=False) as router:
        install_fake_jev(router, rule, calls)
        result = invoke(naive_project, "--judge", "jev", "--judge-sample", "20", "--format", "json")
    assert result.exit_code == 1, result.output  # the naive guard still has bypasses
    report = Report.model_validate(json.loads(result.output))
    assert report.meta["judge"] == "jev"
    assert report.metrics["judge_calls"] == 20 == len(calls)
    assert calls[0]["model"] == "jev-latest"


def test_cli_dry_run_states_the_call_count_and_calls_nothing(naive_project: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://api.typesafe.ai/v1/systemone")
        result = invoke(
            naive_project, "--judge", "jev", "--judge-sample", "30", "--dry-run", key=False
        )
    assert result.exit_code == 0, result.output
    assert "judge: jev; up to 30 calls (cap 200)" in result.output
    assert not route.called


def test_cli_cap_exits_two_without_spending(naive_project: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://api.typesafe.ai/v1/systemone")
        result = invoke(naive_project, "--judge", "jev", "--judge-sample", "50", "--max-runs", "5")
    assert result.exit_code == 2
    assert "judge call" in result.output
    assert not route.called


def test_cli_jev_without_a_key_names_the_variable(naive_project: Path) -> None:
    result = invoke(naive_project, "--judge", "jev", key=False)
    assert result.exit_code == 2
    assert "JEV_API_KEY" in result.output


def test_make_project_fixture_still_available(make_project: MakeProject) -> None:
    assert make_project().is_dir()
