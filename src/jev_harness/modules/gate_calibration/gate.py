"""Run a judge (for example Jev) as the gate, producing a log that ``calibrate report`` can score.

This is the first half of "is Jev's 70% really 70%?": every item gets one choice question, and the
answer plus the confidence the judge stated is written as a gate-log row. A person (or a rule you
trust) then supplies labels and the existing ``sheet`` / ``report`` commands measure calibration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jev_harness.core.errors import ConfigError
from jev_harness.core.judge import Judge, JudgeQuestion
from jev_harness.core.judging import DEFAULT_CONCURRENCY, JudgeBudget, ask_all

SCORES = ("probability", "confidence")
PASSTHROUGH = ("label", "timestamp")
NOT_STATE = {"id", *PASSTHROUGH}


@dataclass(slots=True)
class GateRun:
    rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def load_items(path: Path) -> list[dict[str, Any]]:
    """JSONL items. Each needs an ``id`` and at least one other field for the judge to read."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ConfigError(f"file not found: {path}") from exc
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path.name}:{number}: not valid JSON: {exc}") from exc
        if not isinstance(item, dict) or "id" not in item:
            raise ConfigError(f"{path.name}:{number}: each item must be an object with an 'id'")
        identifier = str(item["id"])
        if identifier in seen:
            raise ConfigError(f"{path.name}:{number}: duplicate id {identifier!r}")
        seen.add(identifier)
        if not {k for k in item if k not in NOT_STATE}:
            raise ConfigError(
                f"{path.name}:{number}: item {identifier!r} has nothing for the judge to read; "
                "add fields such as text, diff, or title"
            )
        items.append(item)
    if not items:
        raise ConfigError(f"{path} contains no items")
    return items


def parse_options(specs: list[str]) -> dict[str, str]:
    """``NAME=criterion`` strings into the judge's option map (order preserved)."""
    options: dict[str, str] = {}
    for spec in specs:
        name, sep, criterion = spec.partition("=")
        name, criterion = name.strip(), criterion.strip()
        if not sep or not name or not criterion:
            raise ConfigError(f"--option must look like NAME=criterion, got {spec!r}")
        if name in options:
            raise ConfigError(f"--option {name!r} given twice")
        options[name] = criterion
    if len(options) < 2:
        raise ConfigError("give at least two --option NAME=criterion values")
    return options


def make_question(
    item: dict[str, Any],
    question: str,
    options: dict[str, str],
    hide: frozenset[str] = frozenset(),
) -> JudgeQuestion:
    state = {k: v for k, v in item.items() if k not in NOT_STATE and k not in hide}
    return JudgeQuestion(question=question, options=options, state=state)


async def run_gate(
    items: list[dict[str, Any]],
    judge: Judge,
    budget: JudgeBudget,
    *,
    question: str,
    options: dict[str, str],
    concurrency: int = DEFAULT_CONCURRENCY,
    hide: frozenset[str] = frozenset(),
    score: str = "probability",
) -> GateRun:
    if score not in SCORES:
        raise ConfigError(f"--score must be one of {', '.join(SCORES)}, got {score!r}")
    for item in items:
        if not {k for k in item if k not in NOT_STATE and k not in hide}:
            raise ConfigError(
                f"item {item['id']!r} has nothing left for the judge once {sorted(hide)} are hidden"
            )
    decisions = await ask_all(
        judge,
        [make_question(i, question, options, hide) for i in items],
        budget,
        concurrency=concurrency,
    )
    run = GateRun()
    now = datetime.now(UTC).isoformat()
    for item, decision in zip(items, decisions, strict=True):
        if decision.escalated:
            run.errors.append({"id": str(item["id"]), "reason": decision.reason})
            continue
        # What the log calls "confidence" is the number calibrate tests against accuracy. A
        # probability is what "70% sure" means; the judge's own confidence statistic is kept too.
        stated = decision.probability if score == "probability" else decision.confidence
        if stated is None:
            stated = decision.confidence  # the judge reports no distribution: use what it has
        row: dict[str, Any] = {
            "id": str(item["id"]),
            "decision": decision.answer,
            "confidence": stated,
            "judge_confidence": decision.confidence,
            "probability": decision.probability,
            "timestamp": item.get("timestamp", now),
            "judge": decision.judge,
        }
        if "label" in item:
            row["label"] = item["label"]
        for name in sorted(hide):
            if name in item:
                row[name] = item[name]  # metadata kept for your analysis; the judge never saw it
        run.rows.append(row)
    return run


def write_run(run: GateRun, out: Path) -> Path | None:
    """Write the gate log, and any failed items beside it (never silently dropped)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r) + "\n" for r in run.rows), encoding="utf-8")
    if not run.errors:
        return None
    errors_path = out.with_suffix(".errors.jsonl")
    errors_path.write_text("".join(json.dumps(e) + "\n" for e in run.errors), encoding="utf-8")
    return errors_path
