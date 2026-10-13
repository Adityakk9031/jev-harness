"""The five reviewer_eval checks. Scoring is by comment location alone (rules-only)."""

from __future__ import annotations

from dataclasses import dataclass

from jev_harness.core.check import Check, Finding, Severity, Source
from jev_harness.modules.reviewer_eval.model import Manifest
from jev_harness.modules.reviewer_eval.score import ReviewerScore

MODULE = "reviewer"
DEFAULT_RECALL_FLOOR = 0.5
DEFAULT_NOISE_CEILING = 3.0
DEFAULT_LATENCY_CEILING = 900.0
DEFAULT_MIN_PRS = 10
MIN_BUG_PRS_FOR_RECALL = 3


@dataclass(slots=True)
class Subject:
    manifest: Manifest
    scores: dict[str, ReviewerScore]


class RecallBelowFloor(Check):
    """The reviewer credibly catches fewer of the planted bugs than the floor."""

    rule_id = f"{MODULE}.recall_below_floor"
    severity = Severity.flag

    def __init__(self, floor: float = DEFAULT_RECALL_FLOOR) -> None:
        self.floor = floor

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        for name, score in subject.scores.items():
            if score.bug_prs < MIN_BUG_PRS_FOR_RECALL or score.recall.high >= self.floor:
                continue
            findings.append(
                self.finding(
                    f"{name} caught {score.caught} of {score.bug_prs} planted bugs "
                    f"({score.recall}), below the {self.floor:.0%} floor",
                    location=name,
                    evidence={
                        "caught": score.caught,
                        "bug_prs": score.bug_prs,
                        "recall": score.recall.estimate,
                        "ci_low": score.recall.low,
                        "ci_high": score.recall.high,
                        "floor": self.floor,
                    },
                )
            )
        return findings


class NoiseAboveCeiling(Check):
    """The reviewer comments on lines that are not the bug, or on PRs that have none."""

    rule_id = f"{MODULE}.noise_above_ceiling"
    severity = Severity.flag

    def __init__(self, ceiling: float = DEFAULT_NOISE_CEILING) -> None:
        self.ceiling = ceiling

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        for name, score in subject.scores.items():
            if score.noise_per_pr <= self.ceiling:
                continue
            findings.append(
                self.finding(
                    f"{name} left {score.noise_comments} inline comment(s) off the bug or on "
                    f"control PRs across {score.prs_total} PRs ({score.noise_per_pr:.1f} per PR), "
                    f"above the {self.ceiling:g} ceiling",
                    location=name,
                    evidence={
                        "noise_comments": score.noise_comments,
                        "prs": score.prs_total,
                        "noise_per_pr": score.noise_per_pr,
                        "controls_flagged": score.controls_flagged,
                        "controls": score.controls,
                    },
                )
            )
        return findings


class LatencyExceeded(Check):
    """The median time to a first comment is above the ceiling."""

    rule_id = f"{MODULE}.latency_exceeded"
    severity = Severity.flag

    def __init__(self, ceiling_seconds: float = DEFAULT_LATENCY_CEILING) -> None:
        self.ceiling = ceiling_seconds

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        for name, score in subject.scores.items():
            median = score.median_latency_seconds
            if median is None or median <= self.ceiling:
                continue
            findings.append(
                self.finding(
                    f"{name}'s median time to a first comment is {median:.0f}s, above the "
                    f"{self.ceiling:g}s ceiling",
                    location=name,
                    evidence={"median_seconds": median, "answered_prs": len(score.latencies)},
                )
            )
        return findings


class InsufficientTrials(Check):
    """Too few planted bugs for the recall interval to say anything useful."""

    rule_id = f"{MODULE}.insufficient_trials"
    severity = Severity.flag

    def __init__(self, min_prs: int = DEFAULT_MIN_PRS) -> None:
        self.min_prs = min_prs

    def run(self, subject: Subject) -> list[Finding]:
        bug_prs = sum(1 for pr in subject.manifest.prs if pr.kind == "bug")
        if bug_prs >= self.min_prs:
            return []
        return [
            self.finding(
                f"only {bug_prs} planted bug(s); at least {self.min_prs} are needed before a "
                "reviewer's recall means much, so treat every rate here as provisional",
                location="manifest",
                evidence={"bug_prs": bug_prs, "min_prs": self.min_prs},
            )
        ]


class BugHunkUntouched(Check):
    """No reviewer pointed at a planted bug: a diagnostic, since the bug may be undetectable."""

    rule_id = f"{MODULE}.bug_hunk_untouched"
    severity = Severity.info

    def run(self, subject: Subject) -> list[Finding]:
        if not subject.scores:
            return []
        caught = set().union(*(s.caught_ids for s in subject.scores.values()))
        findings: list[Finding] = []
        for pr in subject.manifest.prs:
            if pr.kind != "bug" or pr.id in caught:
                continue
            findings.append(
                self.finding(
                    f"no reviewer commented on the re-introduced lines of {pr.id} "
                    f"(fix {pr.fix_sha[:8]}); the bug may be too subtle to see from the diff",
                    location=pr.id,
                    evidence={
                        "fix": pr.fix_sha,
                        "targets": [t.path for t in pr.targets],
                        "url": pr.url,
                    },
                )
            )
        return findings


class CatchNotConfirmed(Check):
    """A comment on the bug's lines that the judge says is about something else.

    Only produced when a judge was run. It does not undo the catch (location decides that); it
    tells you the catch may be coincidence, with the comment text as evidence.
    """

    rule_id = f"{MODULE}.catch_not_confirmed"
    severity = Severity.flag

    def __init__(self, min_confidence: float = 0.7) -> None:
        self.min_confidence = min_confidence

    def run(self, subject: Subject) -> list[Finding]:
        findings: list[Finding] = []
        for name, score in subject.scores.items():
            for pr_id, status in sorted(score.judge_status.items()):
                if status != "rejected":
                    continue
                entries = [
                    c
                    for c in subject.manifest.confirmations
                    if c.reviewer.lower() == name.lower()
                    and c.pr_id == pr_id
                    and c.answer == "unrelated"
                    and c.confidence >= self.min_confidence
                ]
                best = max(entries, key=lambda c: c.confidence)
                findings.append(
                    self.finding(
                        f"{name}'s comment on {pr_id} sits on the bug's lines, but the judge "
                        f"({best.judge}) says it is about something else: "
                        f"{best.comment_excerpt!r}",
                        location=f"{name}/{pr_id}",
                        source=Source.judge,
                        confidence=best.confidence,
                        evidence={"comment": best.comment_excerpt, "judge": best.judge},
                    )
                )
        return findings


def default_checks(
    *,
    recall_floor: float = DEFAULT_RECALL_FLOOR,
    noise_ceiling: float = DEFAULT_NOISE_CEILING,
    latency_ceiling: float = DEFAULT_LATENCY_CEILING,
    min_prs: int = DEFAULT_MIN_PRS,
    min_judge_confidence: float = 0.7,
) -> list[Check]:
    return [
        RecallBelowFloor(recall_floor),
        NoiseAboveCeiling(noise_ceiling),
        LatencyExceeded(latency_ceiling),
        InsufficientTrials(min_prs),
        BugHunkUntouched(),
        CatchNotConfirmed(min_judge_confidence),
    ]
