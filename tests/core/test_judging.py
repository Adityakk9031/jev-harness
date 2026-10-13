from __future__ import annotations

import asyncio

import pytest

from jev_harness.core.errors import BudgetExceededError
from jev_harness.core.judge import Decision, JudgeQuestion, escalated_decision
from jev_harness.core.judging import JudgeBudget, ask_all, trusted


class Scripted:
    name = "scripted"

    def __init__(self, answers: dict[str, tuple[str, float]], delay: float = 0.0) -> None:
        self.answers = answers
        self.calls = 0
        self.active = 0
        self.peak = 0
        self.delay = delay

    async def decide(self, question: JudgeQuestion) -> Decision:
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        if question.question not in self.answers:
            return escalated_decision(self.name, "no scripted answer")
        answer, confidence = self.answers[question.question]
        return Decision(judge=self.name, answer=answer, confidence=confidence, latency_ms=1)


def q(text: str) -> JudgeQuestion:
    return JudgeQuestion(question=text, options={"yes": "y", "no": "n"})


async def test_results_come_back_in_question_order_with_bounded_concurrency() -> None:
    judge = Scripted({"a": ("yes", 0.9), "b": ("no", 0.8), "c": ("yes", 0.6)}, delay=0.01)
    decisions = await ask_all(
        judge, [q("a"), q("b"), q("c")], JudgeBudget(max_calls=10), concurrency=2
    )
    assert [d.answer for d in decisions] == ["yes", "no", "yes"]
    assert judge.peak <= 2


async def test_the_cap_is_checked_before_any_call_is_made() -> None:
    judge = Scripted({})
    budget = JudgeBudget(max_calls=2)
    with pytest.raises(BudgetExceededError, match="needs 3 judge call"):
        await ask_all(judge, [q("a"), q("b"), q("c")], budget)
    assert judge.calls == 0
    assert budget.used == 0


async def test_budget_accumulates_across_batches() -> None:
    judge = Scripted({"a": ("yes", 0.9)})
    budget = JudgeBudget(max_calls=3)
    await ask_all(judge, [q("a"), q("a")], budget)
    assert budget.remaining == 1
    with pytest.raises(BudgetExceededError):
        await ask_all(judge, [q("a"), q("a")], budget)


def test_trusted_requires_an_answer_and_enough_confidence() -> None:
    sure = Decision(judge="j", answer="yes", confidence=0.9, latency_ms=1)
    unsure = Decision(judge="j", answer="yes", confidence=0.5, latency_ms=1)
    assert trusted(sure, 0.7)
    assert not trusted(unsure, 0.7)
    assert not trusted(escalated_decision("j", "down"), 0.0)
