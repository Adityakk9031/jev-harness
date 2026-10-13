"""Observations and survival rates, shared by simulated and real runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.stats import Interval, wilson

Mode = Literal["real", "simulated"]


class Observation(BaseModel):
    """One source measured once: after ``round`` compactions in trial ``trial``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    round: int = Field(ge=0)
    trial: int = Field(ge=0)
    recalled: bool
    #: ``None`` when the source had no behavior probe or it was not run.
    behavior_followed: bool | None = None


class StrategyRun(BaseModel):
    """All observations for one strategy (simulated) or the real agent (real)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: Mode
    deterministic: bool = False
    rounds: int
    trials: int
    observations: list[Observation] = Field(default_factory=list)
    #: Measurement points discarded because no compaction was confirmed (real mode).
    invalid_points: int = 0
    notes: list[str] = Field(default_factory=list)

    def cell(self, source_id: str, round_: int) -> tuple[int, int]:
        """``(recalled, n)`` for a source at a round."""
        hits = [o for o in self.observations if o.source_id == source_id and o.round == round_]
        return sum(o.recalled for o in hits), len(hits)

    def behavior_cell(self, source_id: str, round_: int) -> tuple[int, int] | None:
        hits = [
            o
            for o in self.observations
            if o.source_id == source_id and o.round == round_ and o.behavior_followed is not None
        ]
        if not hits:
            return None
        return sum(bool(o.behavior_followed) for o in hits), len(hits)

    def source_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for observation in self.observations:
            seen.setdefault(observation.source_id)
        return list(seen)

    def matrix(self) -> dict[str, dict[int, Interval]]:
        table: dict[str, dict[int, Interval]] = defaultdict(dict)
        for source_id in self.source_ids():
            for round_ in range(self.rounds + 1):
                recalled, n = self.cell(source_id, round_)
                if n:
                    table[source_id][round_] = wilson(recalled, n)
        return dict(table)

    def behavior_ids(self) -> list[str]:
        return sorted({o.source_id for o in self.observations if o.behavior_followed is not None})
