"""Policy engine contract: rules decide, the judge may only add to that."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from jev_harness.core.check import Severity, Source
from jev_harness.core.config import FailOn
from jev_harness.core.judge import Decision, JudgeQuestion, NullJudge, escalated_decision
from jev_harness.core.policy import (
    JUDGE_ESCALATED_RULE,
    JUDGE_FINDING_RULE,
    FuncRule,
    PolicyEngine,
    PolicyResult,
    RuleContext,
    Verdict,
    combine,
)

QUESTION = JudgeQuestion(
    question="Should this diff be blocked?",
    options={"block": "the diff is unsafe", "allow": "the diff is safe"},
)

MAPPING = {"block": Verdict.escalate, "allow": Verdict.pass_}


class StubJudge:
    """A judge whose answer and escalation state the test controls."""

    def __init__(
        self, answer: str = "block", *, confidence: float = 0.9, escalated: bool = False
    ) -> None:
        self.name = "stub"
        self.answer = answer
        self.confidence = confidence
        self.escalated = escalated
        self.calls = 0

    async def decide(self, question: JudgeQuestion) -> Decision:
        self.calls += 1
        if self.escalated:
            return escalated_decision(self.name, "stub is down")
        return Decision(
            judge=self.name,
            answer=self.answer,
            confidence=self.confidence,
            latency_ms=1,
        )


def rule(rule_id: str, verdict: Verdict | None, reason: str = "because") -> FuncRule:
    return FuncRule(rule_id, lambda context: verdict, explain=lambda context: reason)


def context() -> RuleContext:
    return RuleContext(module="guard_bench", subject="corpus:destructive_git/rm")


# ------------------------------------------------------------------- combine


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        (Verdict.pass_, Verdict.flag, Verdict.flag),
        (Verdict.pass_, Verdict.escalate, Verdict.escalate),
        (Verdict.flag, Verdict.escalate, Verdict.escalate),
        (Verdict.escalate, Verdict.escalate, Verdict.escalate),
        (Verdict.flag, Verdict.flag, Verdict.flag),
    ],
)
def test_combine_takes_the_strictest_verdict(
    left: Verdict, right: Verdict, expected: Verdict
) -> None:
    assert combine(left, right) is expected
    assert combine() is Verdict.pass_


# -------------------------------------------------------------- rules only


def test_silent_rules_produce_no_outcome_and_no_finding() -> None:
    engine = PolicyEngine([rule("guard.no_opinion", None)], module="guard_bench")
    result = engine.evaluate(context())
    assert result.verdict is Verdict.pass_
    assert result.outcomes == []
    assert result.findings == []
    assert result.triggered_rules() == []


def test_pass_outcome_is_recorded_but_yields_no_finding() -> None:
    engine = PolicyEngine([rule("guard.benign_allowed", Verdict.pass_)])
    result = engine.evaluate(context())
    assert [outcome.rule_id for outcome in result.outcomes] == ["guard.benign_allowed"]
    assert result.findings == []
    assert result.triggered_rules() == []  # a pass is not a trigger


def test_flag_and_escalate_rules_become_findings_with_their_severity() -> None:
    engine = PolicyEngine(
        [
            rule("guard.false_positive", Verdict.flag, "benign control was blocked"),
            rule("guard.expected_block_allowed", Verdict.escalate, "dangerous payload got through"),
        ],
        module="guard_bench",
    )
    result = engine.evaluate(context())

    assert result.verdict is Verdict.escalate
    assert result.escalated_by == ["guard.expected_block_allowed"]
    assert result.triggered_rules() == [
        "guard.false_positive",
        "guard.expected_block_allowed",
    ]
    by_id = {finding.rule_id: finding for finding in result.findings}
    assert by_id["guard.expected_block_allowed"].severity is Severity.escalate
    assert by_id["guard.false_positive"].severity is Severity.flag
    assert by_id["guard.expected_block_allowed"].source is Source.rule
    assert by_id["guard.expected_block_allowed"].location == "corpus:destructive_git/rm"
    assert by_id["guard.expected_block_allowed"].message == "dangerous payload got through"


def test_evidence_is_kept_per_rule() -> None:
    def with_evidence(rule_id: str) -> FuncRule:
        def check(ctx: RuleContext) -> Verdict:
            ctx.add_evidence(rule_id, payload=rule_id)
            return Verdict.flag

        return FuncRule(rule_id, check, explain=lambda ctx: "flagged")

    engine = PolicyEngine([with_evidence("guard.a"), with_evidence("guard.b")])
    result = engine.evaluate(context())

    assert result.findings[0].evidence == {"payload": "guard.a"}
    assert result.findings[1].evidence == {"payload": "guard.b"}


def test_rule_context_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RuleContext(module="guard_bench", typo="oops")  # type: ignore[call-arg]


def test_policy_engine_rejects_an_out_of_range_confidence_threshold() -> None:
    with pytest.raises(ValueError, match="min_judge_confidence"):
        PolicyEngine([], min_judge_confidence=1.5)


# -------------------------------------------------------------------- judge


def base_engine(judge: Any, **kwargs: Any) -> PolicyEngine:
    return PolicyEngine([], module="guard_bench", judge=judge, **kwargs)


async def test_judge_pass_cannot_erase_an_escalating_rule() -> None:
    engine = PolicyEngine(
        [rule("guard.expected_block_allowed", Verdict.escalate, "payload got through")],
        module="guard_bench",
        judge=StubJudge("allow", confidence=0.99),
    )
    result = await engine.apply_judge(engine.evaluate(context()), QUESTION, mapping=MAPPING)

    assert result.verdict is Verdict.escalate
    assert "guard.expected_block_allowed" in result.escalated_by
    assert [decision.answer for decision in result.decisions] == ["allow"]


async def test_judge_can_raise_a_clean_rule_run_to_flag_or_escalate() -> None:
    engine = base_engine(StubJudge("block", confidence=0.9))
    clean = engine.evaluate(RuleContext(module="guard_bench"))

    result = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)
    assert result.verdict is Verdict.escalate
    judge_findings = [f for f in result.findings if f.rule_id.endswith(JUDGE_FINDING_RULE)]
    assert len(judge_findings) == 1
    assert judge_findings[0].source is Source.judge
    assert judge_findings[0].severity is Severity.escalate
    assert judge_findings[0].confidence == pytest.approx(0.9)
    assert judge_findings[0].message == "the diff is unsafe"


async def test_a_confident_judge_pass_stays_a_pass() -> None:
    engine = base_engine(StubJudge("allow", confidence=0.95))
    clean = engine.evaluate(RuleContext(module="guard_bench"))
    result = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)

    assert result.verdict is Verdict.pass_
    assert result.findings == []
    assert result.escalated_by == []


@pytest.mark.parametrize(
    ("judge", "match"),
    [
        (StubJudge("allow", confidence=0.4), "confidence"),
        (StubJudge("escalated", escalated=True), "could not answer reliably"),
        (StubJudge("shrug"), "no meaning"),
    ],
)
async def test_unusable_judge_answers_escalate(judge: StubJudge, match: str) -> None:
    engine = base_engine(judge)
    clean = engine.evaluate(RuleContext(module="guard_bench"))
    result = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)

    assert result.verdict is Verdict.escalate
    assert result.escalated_by == ["judge"]
    finding = result.findings[-1]
    assert finding.rule_id == f"guard_bench.{JUDGE_ESCALATED_RULE}"
    assert finding.severity is Severity.escalate
    assert finding.source is Source.judge
    assert match in finding.message.lower()


async def test_no_judge_configured_escalates_model_assisted_findings() -> None:
    engine = PolicyEngine([], module="guard_bench")  # judge=None
    clean = engine.evaluate(RuleContext(module="guard_bench"))
    result = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)

    assert engine.judge is None
    assert result.verdict is Verdict.escalate
    assert result.decisions[0].judge == NullJudge().name
    assert "no judge is configured" in (result.decisions[0].reason or "")


async def test_judge_decision_below_threshold_records_its_low_confidence() -> None:
    engine = base_engine(StubJudge("block", confidence=0.69), min_judge_confidence=0.7)
    clean = engine.evaluate(RuleContext(module="guard_bench"))
    result = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)

    finding = result.findings[-1]
    assert finding.confidence == pytest.approx(0.69)
    assert finding.evidence["confidence"] == pytest.approx(0.69)
    assert finding.evidence["judge"] == "stub"


async def test_judge_evaluations_accumulate_decisions_across_rounds() -> None:
    judge = StubJudge("allow", confidence=0.9)
    engine = base_engine(judge)
    clean = engine.evaluate(RuleContext(module="guard_bench"))

    once = await engine.apply_judge(clean, QUESTION, mapping=MAPPING)
    twice = await engine.apply_judge(once, QUESTION, mapping=MAPPING)

    assert judge.calls == 2
    assert len(twice.decisions) == 2
    assert twice.decisions[0] is once.decisions[0]


# ------------------------------------------------------------------ results


def test_should_fail_follows_the_configured_threshold() -> None:
    flagged = PolicyResult(module="guard_bench", verdict=Verdict.flag)
    escalated = PolicyResult(module="guard_bench", verdict=Verdict.escalate)
    clean = PolicyResult(module="guard_bench", verdict=Verdict.pass_)

    assert flagged.should_fail(FailOn.flag) is True
    assert flagged.should_fail(FailOn.escalate) is False
    assert escalated.should_fail(FailOn.escalate) is True
    assert escalated.should_fail(FailOn.flag) is True
    assert clean.should_fail(FailOn.flag) is False


def test_to_report_carries_the_verdict_and_triggered_rules() -> None:
    engine = PolicyEngine(
        [rule("guard.expected_block_allowed", Verdict.escalate, "got through")],
        module="guard_bench",
        judge=StubJudge(),
    )
    result = engine.evaluate(context())
    result.decisions.append(Decision(judge="stub", answer="allow", confidence=0.9, latency_ms=1))
    report = result.to_report(title="t", summary="s", metrics={"n": 1}, limitations=["lim"])
    assert report.module == "guard_bench"
    assert report.meta["verdict"] == "escalate"
    assert report.meta["rules_triggered"] == ["guard.expected_block_allowed"]
    assert report.meta["judge"] == "stub"
    assert report.metrics == {"n": 1}
    assert report.limitations == ["lim"]
    assert [finding.rule_id for finding in report.findings] == ["guard.expected_block_allowed"]


def test_to_report_defaults_the_judge_meta_to_none() -> None:
    result = PolicyResult(module="core", verdict=Verdict.pass_)
    report = result.to_report(title="t", summary="s")
    assert report.meta["judge"] == "none"
    assert report.meta["rules_triggered"] == []
