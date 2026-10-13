"""Run many judge questions under a call budget.

Modules that use a judge ask it dozens or hundreds of questions. This keeps that predictable:
the number of calls is checked against a cap *before* any call is made, calls run with bounded
concurrency, and results come back in question order. A judge that fails or is unsure yields an
escalated or low-confidence :class:`Decision`, which :func:`trusted` treats as "no answer", so a
failure can never read as a confirmation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import BudgetExceededError
from jev_harness.core.judge import Decision, Judge, JudgeQuestion

DEFAULT_MAX_JUDGE_CALLS = 200
DEFAULT_CONCURRENCY = 4


class JudgeBudget(BaseModel):
    """A cap on judge calls for one command."""

    model_config = ConfigDict(extra="forbid")

    max_calls: int = Field(default=DEFAULT_MAX_JUDGE_CALLS, ge=1)
    used: int = Field(default=0, ge=0)

    @property
    def remaining(self) -> int:
        return self.max_calls - self.used

    def reserve(self, calls: int) -> None:
        """Refuse a batch that would pass the cap, before any call is made."""
        if calls > self.remaining:
            raise BudgetExceededError(
                f"this step needs {calls} judge call(s) but only {self.remaining} of "
                f"{self.max_calls} remain; raise --max-runs or reduce the sample "
                "(run with --dry-run to see the count)"
            )
        self.used += calls


def trusted(decision: Decision, min_confidence: float) -> bool:
    """Whether a decision may be relied on: it answered, and was at least this sure."""
    return not decision.escalated and decision.confidence >= min_confidence


async def ask_all(
    judge: Judge,
    questions: Sequence[JudgeQuestion],
    budget: JudgeBudget,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[Decision]:
    """Ask every question, in order, with bounded concurrency."""
    budget.reserve(len(questions))
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(question: JudgeQuestion) -> Decision:
        async with semaphore:
            return await judge.decide(question)

    return list(await asyncio.gather(*(one(q) for q in questions)))
