"""Stand-in judges for tests: in-process, and a fake Jev endpoint for CLI tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import respx

from jev_harness.core.judge import Decision, JudgeQuestion, escalated_decision

#: ``(question, state) -> (answer, confidence)``, or ``None`` for "the judge failed".
Rule = Callable[[JudgeQuestion], tuple[str, float] | None]


class ScriptedJudge:
    """Answers from a function and records every question it was asked."""

    name = "scripted"

    def __init__(self, rule: Rule) -> None:
        self.rule = rule
        self.questions: list[JudgeQuestion] = []

    async def decide(self, question: JudgeQuestion) -> Decision:
        self.questions.append(question)
        result = self.rule(question)
        if result is None:
            return escalated_decision(self.name, "scripted failure")
        answer, confidence = result
        return Decision(judge=self.name, answer=answer, confidence=confidence, latency_ms=1)


def install_fake_jev(router: respx.MockRouter, rule: Rule, calls: list[dict[str, Any]]) -> None:
    """Serve ``https://api.typesafe.ai/v1/systemone`` from ``rule`` and record each request body."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        spec = body["questions"]["decision"]
        question = JudgeQuestion(
            question=spec["instructions"], options=spec["criteria"], state=body["state"]
        )
        result = rule(question)
        if result is None:
            return httpx.Response(503, json={"message": "down"})
        choice, confidence = result
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "answers": {
                    "decision": {"type": "choice", "choice": choice, "confidence": confidence}
                },
                "usage": {},
            },
        )

    router.post("https://api.typesafe.ai/v1/systemone").mock(side_effect=handler)
