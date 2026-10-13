"""Finding contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jev_harness.core.check import Finding, Severity, Source, sort_findings


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
