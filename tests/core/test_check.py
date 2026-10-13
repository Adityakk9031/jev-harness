"""Finding contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jev_harness.core.check import Check, Eval, Finding, Severity, Source, sort_findings


def test_finding_defaults_to_a_rule_source() -> None:
    finding = Finding(rule_id="guard.benign_blocked", severity=Severity.flag, message="blocked")
    assert finding.source is Source.rule
    assert finding.confidence is None
    assert finding.evidence == {}
    assert finding.location is None


def test_judge_finding_requires_a_confidence() -> None:
    with pytest.raises(ValidationError, match="must carry a confidence"):
        Finding(
            rule_id="guard.disguise_class_floor",
            severity=Severity.info,
            message="judged lenient",
            source=Source.judge,
        )

    finding = Finding(
        rule_id="guard.disguise_class_floor",
        severity=Severity.info,
        message="judged lenient",
        source=Source.judge,
        confidence=0.62,
    )
    assert finding.confidence == 0.62


@pytest.mark.parametrize("rule_id", ["guard", "Guard.bad", "guard.", "guard.Bad_Id", "1guard.x"])
def test_rule_ids_are_namespaced(rule_id: str) -> None:
    with pytest.raises(ValidationError, match="rule_id"):
        Finding(rule_id=rule_id, severity=Severity.info, message="x")


def test_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Finding(rule_id="guard.x", severity=Severity.info, message="x", confidence=1.2)


def test_findings_are_immutable() -> None:
    finding = Finding(rule_id="guard.x", severity=Severity.info, message="x")
    with pytest.raises(ValidationError, match="frozen"):
        finding.message = "changed"


def test_sort_findings_is_deterministic_and_severe_first() -> None:
    findings = [
        Finding(rule_id="b.second", severity=Severity.info, message="i"),
        Finding(rule_id="a.first", severity=Severity.flag, message="f"),
        Finding(rule_id="c.third", severity=Severity.escalate, message="e"),
        Finding(rule_id="a.first", severity=Severity.escalate, message="e2", location="z"),
        Finding(rule_id="a.first", severity=Severity.escalate, message="e3", location="a"),
    ]
    ordered = sort_findings(findings)
    assert [f.severity for f in ordered] == [
        Severity.escalate,
        Severity.escalate,
        Severity.escalate,
        Severity.flag,
        Severity.info,
    ]
    assert [f.location for f in ordered[:3]] == ["a", "z", None]
    assert ordered == sort_findings(list(reversed(findings)))


class BenignControl(Check):
    """A check that flags whenever its subject is a known benign control."""

    rule_id = "guard.benign_blocked"
    severity = Severity.flag

    def run(self, subject: str) -> list[Finding]:
        return [self.finding("benign control was blocked", location=subject)]


class DangerousAllowed(Check):
    rule_id = "guard.expected_block_allowed"
    severity = Severity.escalate

    def run(self, subject: str) -> list[Finding]:
        return [self.finding("expected-block payload was allowed", location=subject)]


class Silent(Check):
    rule_id = "guard.no_opinion"

    def run(self, subject: str) -> list[Finding]:
        return []


class GuardEval(Eval):
    module = "guard_bench"


def test_check_is_abstract_but_stamps_findings_with_its_own_identity() -> None:
    with pytest.raises(TypeError):
        Check()  # type: ignore[abstract]

    findings = BenignControl().run("corpus:benign/git-status")
    assert len(findings) == 1
    assert findings[0].rule_id == "guard.benign_blocked"
    assert findings[0].severity is Severity.flag
    assert findings[0].location == "corpus:benign/git-status"


def test_an_eval_refuses_to_run_with_no_checks() -> None:
    with pytest.raises(ValueError, match="at least one check"):
        GuardEval([])


def test_eval_collects_and_orders_findings_across_checks() -> None:
    evaluation = GuardEval([BenignControl(), Silent(), DangerousAllowed()])
    findings = evaluation.run_checks("corpus:x")

    assert evaluation.check_ids() == [
        "guard.benign_blocked",
        "guard.no_opinion",
        "guard.expected_block_allowed",
    ]
    assert [finding.rule_id for finding in findings] == [
        "guard.expected_block_allowed",
        "guard.benign_blocked",
    ]
    assert evaluation.run_checks("corpus:x") == findings  # deterministic


def test_eval_builds_a_report_under_its_module() -> None:
    evaluation = GuardEval([DangerousAllowed()])
    report = evaluation.build_report(
        title="guard_bench: hooks",
        summary="1 payload slipped through",
        findings=evaluation.run_checks("corpus:x"),
        metrics={"n": 1},
        meta={"mode": "real"},
        limitations=["matcher is emulated"],
    )
    assert report.module == "guard_bench"
    assert report.metrics == {"n": 1}
    assert report.meta == {"mode": "real"}
    assert report.limitations == ["matcher is emulated"]
    assert report.findings[0].severity is Severity.escalate
