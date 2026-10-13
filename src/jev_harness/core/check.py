"""The finding contract shared by every module.

A :class:`Finding` is the only way a module is allowed to report something the
user must look at. Two rules of the product live in this module:

* every finding names the rule that produced it, so ``--compare`` and the
  GitHub Action can rely on stable ids (``<module>.<check>``), and
* a finding that came from a model must carry a confidence, so a judge can
  never produce an unattributed, unquantified assertion (PLAN D3, brief 2.4).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Rule ids are ``<module>.<check>``, lower snake case, and are treated as
#: stable public identifiers. Renaming one needs a CHANGELOG entry.
RULE_ID_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"


class Severity(StrEnum):
    """How loudly a finding should be reported.

    ``info`` is diagnostic context, ``flag`` is something to look at, and
    ``escalate`` is something that must be resolved before trusting the setup.
    """

    info = "info"
    flag = "flag"
    escalate = "escalate"


#: Sort order used in reports: most severe first, deterministically.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.escalate: 0,
    Severity.flag: 1,
    Severity.info: 2,
}


class Source(StrEnum):
    """Where a finding came from: a deterministic rule or a model judge."""

    rule = "rule"
    judge = "judge"


class Finding(BaseModel):
    """One thing worth reporting, with the evidence that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(pattern=RULE_ID_PATTERN)
    severity: Severity
    message: str = Field(min_length=1)
    location: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source: Source = Source.rule

    @model_validator(mode="after")
    def _judge_findings_carry_confidence(self) -> Self:
        if self.source is Source.judge and self.confidence is None:
            raise ValueError("a judge-sourced Finding must carry a confidence")
        return self


def sort_findings(findings: list[Finding]) -> list[Finding]:
    """Return findings ordered by severity, then rule id, then location.

    Reports must be byte-stable across runs so they can be snapshot-tested and
    diffed between runs; the input order is therefore never preserved.
    """
    return sorted(
        findings,
        key=lambda f: (SEVERITY_RANK[f.severity], f.rule_id, f.location or ""),
    )
