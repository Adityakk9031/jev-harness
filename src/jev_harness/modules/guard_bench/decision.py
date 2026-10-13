"""What a single guard said about a single payload."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Outcome(StrEnum):
    """A guard's answer. Only ``block`` and ``ask`` put anything between agent and payload."""

    block = "block"
    ask = "ask"
    allow = "allow"
    #: Nothing either way: a hook exiting 0 with no decision, or no rule matched.
    no_opinion = "no_opinion"
    #: The guard failed (timeout, crash, unreadable output). The agent treats most of these as
    #: non-blocking, so for scoring this is a payload that got through.
    error = "error"


class GuardDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    guard: str
    kind: str
    outcome: Outcome
    reason: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def stops(self) -> bool:
        return self.outcome is Outcome.block

    @property
    def gates(self) -> bool:
        return self.outcome is Outcome.ask
