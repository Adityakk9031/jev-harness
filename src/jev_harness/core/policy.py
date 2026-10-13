"""The precedence rules: deterministic rules decide, a judge may only tighten.

The brief's rule is that a model may add findings or adjust confidence but can
never override a rule that returned ``escalate``. That is enforced structurally
here rather than by convention: :func:`combine` has no path that lowers a
severity, and :meth:`PolicyEngine.apply_judge` only ever merges through it.

Every triggered rule is recorded with its reason
(:class:`RuleOutcome`), and every rule verdict of ``flag``/``escalate`` becomes
a :class:`~jev_harness.core.check.Finding` attributed to that rule. A judge
finding always carries its confidence, so an unattributed model assertion is not
representable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.check import Finding, Severity, Source
from jev_harness.core.config import FailOn
from jev_harness.core.judge import Decision, Judge, JudgeQuestion, NullJudge
from jev_harness.core.report import Report


class Verdict(StrEnum):
    """What a rule or an evaluation concluded about one subject."""

    #: Nothing to report. Note that ``None`` from a rule means "no opinion".
    pass_ = "pass"  # noqa: S105 - a verdict name, not a password
    #: Worth looking at.
    flag = "flag"
    #: Must be resolved before the setup can be trusted.
    escalate = "escalate"


_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.pass_: 0,
    Verdict.flag: 1,
    Verdict.escalate: 2,
}


def combine(*verdicts: Verdict) -> Verdict:
    """The strictest verdict wins. This function never lowers a severity."""
    if not verdicts:
        return Verdict.pass_
    return max(verdicts, key=lambda verdict: _VERDICT_RANK[verdict])


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    """One rule's opinion, recorded whether or not it produced a finding."""

    rule_id: str
    verdict: Verdict
    reason: str


class RuleContext(BaseModel):
    """What a rule is told, and where it attaches its own evidence."""

    model_config = ConfigDict(extra="forbid")

    module: str
    subject: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    #: Per-rule evidence, keyed by rule id, so two rules cannot overwrite each
    #: other's evidence the way a single shared dict would.
    evidence: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def add_evidence(self, rule_id: str, **items: Any) -> None:
        self.evidence.setdefault(rule_id, {}).update(items)

    def evidence_for(self, rule_id: str) -> dict[str, Any]:
        return dict(self.evidence.get(rule_id, {}))


class Rule(Protocol):
    """A deterministic check. ``None`` means "no opinion", not "pass"."""

    rule_id: str

    def check(self, context: RuleContext) -> Verdict | None:
        """The verdict for this subject, or ``None`` to stay silent."""

    def explain(self, context: RuleContext) -> str:
        """A human sentence explaining the verdict, used as the finding message."""


class FuncRule:
    """A rule built from callables, for tests and small inline checks."""

    def __init__(
        self,
        rule_id: str,
        check: Callable[[RuleContext], Verdict | None],
        *,
        explain: Callable[[RuleContext], str] | None = None,
    ) -> None:
        self.rule_id = rule_id
        self._check = check
        self._explain = explain if explain is not None else (lambda context: self.rule_id)

    def check(self, context: RuleContext) -> Verdict | None:
        return self._check(context)

    def explain(self, context: RuleContext) -> str:
        return self._explain(context)


class PolicyResult(BaseModel):
    """The outcome of evaluating one subject."""

    model_config = ConfigDict(extra="forbid")

    module: str
    verdict: Verdict
    findings: list[Finding] = Field(default_factory=list)
    outcomes: list[RuleOutcome] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    #: Rule ids (or ``judge``) that pushed the verdict to escalate.
    escalated_by: list[str] = Field(default_factory=list)

    def should_fail(self, fail_on: FailOn) -> bool:
        threshold = Verdict.escalate if fail_on is FailOn.escalate else Verdict.flag
        return _VERDICT_RANK[self.verdict] >= _VERDICT_RANK[threshold]

    def triggered_rules(self) -> list[str]:
        return [
            outcome.rule_id for outcome in self.outcomes if outcome.verdict is not Verdict.pass_
        ]

    def to_report(
        self,
        *,
        title: str,
        summary: str,
        metrics: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        limitations: list[str] | None = None,
    ) -> Report:
        """Wrap this result in a :class:`Report`, findings already sorted."""
        return Report(
            module=self.module,
            title=title,
            summary=summary,
            findings=list(self.findings),
            metrics=metrics or {},
            meta={
                "verdict": self.verdict.value,
                "rules_triggered": self.triggered_rules(),
                "judge": self.decisions[-1].judge if self.decisions else "none",
                **(meta or {}),
            },
            limitations=limitations or [],
        )


JUDGE_ESCALATED_RULE = "judge_escalated"
JUDGE_FINDING_RULE = "judge_finding"


class PolicyEngine:
    """Runs rules, then optionally lets a judge add to (never subtract from) them."""

    def __init__(
        self,
        rules: Sequence[Rule],
        *,
        module: str = "core",
        judge: Judge | None = None,
        min_judge_confidence: float = 0.7,
    ) -> None:
        if not 0.0 <= min_judge_confidence <= 1.0:
            raise ValueError("min_judge_confidence must be within [0, 1]")
        self.rules = list(rules)
        self.module = module
        self.judge = judge
        self.min_judge_confidence = min_judge_confidence

    # ------------------------------------------------------------- rules only

    def evaluate(self, context: RuleContext) -> PolicyResult:
        """Run every rule. Deterministic, offline, and always available."""
        findings: list[Finding] = []
        outcomes: list[RuleOutcome] = []
        escalated_by: list[str] = []
        verdicts: list[Verdict] = []

        for rule in self.rules:
            verdict = rule.check(context)
            if verdict is None:
                continue
            reason = rule.explain(context)
            outcomes.append(RuleOutcome(rule_id=rule.rule_id, verdict=verdict, reason=reason))
            verdicts.append(verdict)
            if verdict is Verdict.escalate:
                escalated_by.append(rule.rule_id)
            if verdict is not Verdict.pass_:
                findings.append(
                    Finding(
                        rule_id=rule.rule_id,
                        severity=Severity(verdict.value),
                        message=reason,
                        location=context.subject,
                        evidence=context.evidence_for(rule.rule_id),
                    )
                )

        return PolicyResult(
            module=self.module,
            verdict=combine(*verdicts),
            findings=findings,
            outcomes=outcomes,
            escalated_by=escalated_by,
        )

    # ------------------------------------------------------------------ judge

    async def apply_judge(
        self,
        result: PolicyResult,
        question: JudgeQuestion,
        *,
        mapping: Mapping[str, Verdict],
        subject: str | None = None,
    ) -> PolicyResult:
        """Ask the judge and merge its answer in, without ever softening a rule.

        ``mapping`` says what each of the question's options means for the
        verdict; the caller owns that, so a judge cannot reinterpret its own
        question. An unavailable, unusable, or under-confident judge escalates.
        """
        judge = self.judge if self.judge is not None else NullJudge()
        decision = await judge.decide(question)
        findings = list(result.findings)
        escalated_by = list(result.escalated_by)
        judge_verdict = Verdict.pass_

        if decision.escalated or decision.confidence < self.min_judge_confidence:
            judge_verdict = Verdict.escalate
            escalated_by.append("judge")
            findings.append(
                Finding(
                    rule_id=f"{self.module}.{JUDGE_ESCALATED_RULE}",
                    severity=Severity.escalate,
                    message=_uncertain_message(decision, self.min_judge_confidence),
                    location=subject,
                    evidence={
                        "judge": decision.judge,
                        "confidence": decision.confidence,
                        "reason": decision.reason,
                    },
                    confidence=decision.confidence,
                    source=Source.judge,
                )
            )
        else:
            mapped = mapping.get(decision.answer)
            if mapped is None:
                judge_verdict = Verdict.escalate
                escalated_by.append("judge")
                findings.append(
                    Finding(
                        rule_id=f"{self.module}.{JUDGE_ESCALATED_RULE}",
                        severity=Severity.escalate,
                        message=(
                            f"the judge answered {decision.answer!r}, which this evaluation has "
                            "no meaning for; treating that as escalation"
                        ),
                        location=subject,
                        evidence={"judge": decision.judge, "mapping_keys": sorted(mapping)},
                        confidence=decision.confidence,
                        source=Source.judge,
                    )
                )
            elif mapped is not Verdict.pass_:
                judge_verdict = mapped
                findings.append(
                    Finding(
                        rule_id=f"{self.module}.{JUDGE_FINDING_RULE}",
                        severity=Severity(mapped.value),
                        message=question.options.get(decision.answer, decision.answer),
                        location=subject,
                        evidence={"judge": decision.judge, "answer": decision.answer},
                        confidence=decision.confidence,
                        source=Source.judge,
                    )
                )

        return PolicyResult(
            module=result.module,
            verdict=combine(result.verdict, judge_verdict),
            findings=findings,
            outcomes=result.outcomes,
            decisions=[*result.decisions, decision],
            escalated_by=escalated_by,
        )


def _uncertain_message(decision: Decision, threshold: float) -> str:
    if decision.escalated:
        return f"the judge could not answer reliably ({decision.reason or 'unavailable'})"
    return (
        f"the judge answered {decision.answer!r} with confidence {decision.confidence:.2f}, "
        f"below the {threshold:.2f} threshold, so this is treated as uncertain"
    )
