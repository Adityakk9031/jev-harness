"""Synthetic gates whose calibration is known by construction.

A *level* is ``(confidence, n, correct)``: ``n`` skip decisions made at that confidence, of which
``correct`` carry the label ``skip`` and the rest carry the label ``run`` (the gate let a risky item
through). Because the counts are chosen, every expected metric in the tests is exact rather than
estimated.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

Level = tuple[float, int, int]


def gate_rows(
    levels: list[Level],
    *,
    run_decisions: int = 0,
    labeled: bool = True,
    start: datetime | None = None,
    prefix: str = "r",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clock = start or datetime(2026, 1, 1, tzinfo=UTC)
    counter = 0

    def add(decision: str, confidence: float, label: str) -> None:
        nonlocal counter
        counter += 1
        row: dict[str, Any] = {
            "id": f"{prefix}{counter:05d}",
            "decision": decision,
            "confidence": confidence,
            "timestamp": (clock + timedelta(minutes=counter)).isoformat(),
        }
        if labeled:
            row["label"] = label
        rows.append(row)

    for confidence, n, correct in levels:
        for index in range(n):
            add("skip", confidence, "skip" if index < correct else "run")
    for _ in range(run_decisions):
        add("run", 0.9, "run")
    return rows


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path
