"""Live checks against the real Jev endpoint. Skipped unless JEV_API_KEY is set and -m live is used.

    JEV_API_KEY=... uv run pytest -m live tests/test_live_jev.py -o addopts=--strict-markers

Each test makes one or two calls. They assert the shape of the answer (an option you offered and a
confidence between 0 and 1), not that the model agrees with you, so they stay stable.
"""

from __future__ import annotations

import os

import pytest
from tests.modules.reviewer_eval.test_judge_confirm import manifest

from jev_harness.core.config import JudgeBackend, load_settings
from jev_harness.core.judge import JudgeQuestion, get_judge
from jev_harness.core.judging import JudgeBudget
from jev_harness.modules import gate_calibration
from jev_harness.modules.reviewer_eval import confirm_catches

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("JEV_API_KEY"), reason="JEV_API_KEY is not set"),
]


def judge():
    settings = load_settings(None)
    return get_judge(settings.model_copy(update={"judge": JudgeBackend.jev}))


async def test_jev_answers_a_choice_with_a_confidence() -> None:
    decision = await judge().decide(
        JudgeQuestion(
            question="Is deleting a repository's .git directory destructive?",
            options={"yes": "It destroys history", "no": "It is harmless"},
        )
    )
    assert not decision.escalated, decision.reason
    assert decision.answer in {"yes", "no"}
    assert 0.0 <= decision.confidence <= 1.0


async def test_the_gate_runs_on_real_items() -> None:
    items = [
        {"id": "a", "title": "Fix typo in README", "files": ["README.md"]},
        {"id": "b", "title": "Rewrite payment retry logic", "files": ["src/pay.py"]},
    ]
    run = await gate_calibration.run_gate(
        items,
        judge(),
        JudgeBudget(max_calls=5),
        question="Does this change need the full test suite?",
        options={"skip": "Only docs changed", "full": "Code changed"},
    )
    assert not run.errors, run.errors
    assert {r["decision"] for r in run.rows} <= {"skip", "full"}


async def test_confirmation_on_real_comments() -> None:
    m = manifest()
    made = await confirm_catches(m, judge(), JudgeBudget(max_calls=10))
    assert made == 4
    assert {c.answer for c in m.confirmations} <= {"describes_bug", "unrelated", "unclear"}
