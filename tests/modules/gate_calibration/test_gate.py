from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from tests.judges import ScriptedJudge, install_fake_jev
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.errors import BudgetExceededError, ConfigError
from jev_harness.core.judging import JudgeBudget
from jev_harness.modules import gate_calibration as module

runner = CliRunner()
OPTIONS = {"skip": "No code changed", "full": "Code changed"}


def items_file(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "items.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def rule(question) -> tuple[str, float] | None:  # type: ignore[no-untyped-def]
    text = json.dumps(question.state)
    if "boom" in text:
        return None
    return ("skip", 0.95) if "README" in text else ("full", 0.8)


async def test_the_gate_log_has_the_judges_answers_and_confidences_passthrough_labels(
    tmp_path: Path,
) -> None:
    loaded = module.load_items(
        items_file(
            tmp_path,
            [
                {"id": "a", "title": "Fix README typo", "label": "skip"},
                {
                    "id": "b",
                    "title": "Rewrite parser",
                    "label": "full",
                    "timestamp": "2026-01-02T00:00:00Z",
                },
                {"id": "c", "title": "boom"},
            ],
        )
    )
    judge = ScriptedJudge(rule)
    run = await module.run_gate(
        loaded, judge, JudgeBudget(max_calls=10), question="Need tests?", options=OPTIONS
    )

    assert [(r["id"], r["decision"], r["confidence"]) for r in run.rows] == [
        ("a", "skip", 0.95),
        ("b", "full", 0.8),
    ]
    assert run.rows[0]["label"] == "skip"
    assert run.rows[1]["timestamp"] == "2026-01-02T00:00:00Z"
    assert [e["id"] for e in run.errors] == [
        "c"
    ]  # a failure is recorded, not turned into a decision
    # The judge saw the item's fields but never its id or its label.
    assert judge.questions[0].state == {"title": "Fix README typo"}

    out = tmp_path / "out" / "gate.jsonl"
    errors = module.write_run(run, out)
    assert errors == out.with_suffix(".errors.jsonl")
    assert len(out.read_text().splitlines()) == 2


async def test_too_many_items_for_the_cap_fail_before_any_call(tmp_path: Path) -> None:
    loaded = module.load_items(items_file(tmp_path, [{"id": str(i), "t": "x"} for i in range(5)]))
    judge = ScriptedJudge(rule)
    with pytest.raises(BudgetExceededError):
        await module.run_gate(
            loaded, judge, JudgeBudget(max_calls=4), question="q", options=OPTIONS
        )
    assert judge.questions == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"title": "no id"}\n', "'id'"),
        ('{"id": "a"}\n', "nothing for the judge to read"),
        ('{"id": "a", "t": 1}\n{"id": "a", "t": 2}\n', "duplicate id"),
        ("nope\n", "not valid JSON"),
        ("", "contains no items"),
    ],
)
def test_bad_items_are_rejected_with_a_location(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "i.jsonl"
    path.write_text(body)
    with pytest.raises(ConfigError, match=message):
        module.load_items(path)


def test_option_parsing() -> None:
    assert module.parse_options(["skip=ok", "full=run all"]) == {"skip": "ok", "full": "run all"}
    for bad in (["skip=ok"], ["skip", "full=x"], ["a=1", "a=2"], ["=x", "y=1"]):
        with pytest.raises(ConfigError):
            module.parse_options(bad)


# --- through the CLI, with a fake Jev endpoint -------------------------------------------


def invoke(*args: str, key: bool = True):
    env = {"JEV_API_KEY": "test-key"} if key else {"JEV_API_KEY": ""}
    return runner.invoke(app, ["calibrate", "gate", *args], env=env)


def base_args(items: Path, out: Path) -> list[str]:
    return [str(items), "--question", "Need tests?", "--option", "skip=No code changed",
            "--option", "full=Code changed", "-o", str(out)]  # fmt: skip


def test_cli_gate_then_report_round_trip(tmp_path: Path) -> None:
    rows = [
        {
            "id": f"i{n}",
            "title": "Fix README" if n % 2 else "Rewrite parser",
            "label": "skip" if n % 2 else "full",
        }
        for n in range(40)
    ]
    items, out = items_file(tmp_path, rows), tmp_path / "gate.jsonl"
    calls: list[dict[str, Any]] = []
    with respx.mock(assert_all_called=False) as router:
        install_fake_jev(router, rule, calls)
        result = invoke(*base_args(items, out), "--judge", "jev")
    assert result.exit_code == 0, result.output
    assert len(calls) == 40
    assert calls[0]["questions"]["decision"]["criteria"] == OPTIONS
    assert "wrote" in result.output

    report = runner.invoke(
        app, ["calibrate", "report", str(out), "--skip-decision", "skip", "--format", "json"]
    )
    parsed = json.loads(report.output)
    assert parsed["metrics"]["labeled"] == 40
    # 20 skips, none missed: far too few to show a 1% miss rate, and the report says so.
    assert str(parsed["metrics"]["recommended_threshold"]).startswith("none")
    ids = {f["rule_id"] for f in parsed["findings"]}
    assert "calibrate.insufficient_labels" in ids
    assert "calibrate.miss_bound_breach" not in ids


def test_cli_gate_dry_run_calls_nothing(tmp_path: Path) -> None:
    items = items_file(tmp_path, [{"id": "a", "t": "x"}, {"id": "b", "t": "y"}])
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://api.typesafe.ai/v1/systemone")
        result = invoke(
            *base_args(items, tmp_path / "g.jsonl"), "--judge", "jev", "--dry-run", key=False
        )
    assert result.exit_code == 0, result.output
    assert "judge calls: 2 (cap 200)" in result.output
    assert not route.called
    assert not (tmp_path / "g.jsonl").exists()


def test_cli_gate_errors(tmp_path: Path) -> None:
    items = items_file(tmp_path, [{"id": "a", "t": "x"}])
    args = base_args(items, tmp_path / "g.jsonl")
    no_judge = invoke(*args)
    assert no_judge.exit_code == 2
    assert "needs a judge" in no_judge.output
    no_key = invoke(*args, "--judge", "jev", key=False)
    assert no_key.exit_code == 2
    assert "JEV_API_KEY" in no_key.output
    assert invoke(*args, "--judge", "nope").exit_code == 2
    capped = invoke(*args, "--judge", "jev", "--max-runs", "1")  # 1 item fits the cap of 1
    assert capped.exit_code in (0, 2)


def test_cli_gate_all_failures_exit_two_and_record_the_errors(tmp_path: Path) -> None:
    items = items_file(tmp_path, [{"id": "a", "t": "boom"}, {"id": "b", "t": "boom"}])
    out = tmp_path / "g.jsonl"
    with respx.mock(assert_all_called=False) as router:
        install_fake_jev(router, rule, [])
        result = invoke(*base_args(items, out), "--judge", "jev")
    assert result.exit_code == 2
    assert out.with_suffix(".errors.jsonl").exists()
