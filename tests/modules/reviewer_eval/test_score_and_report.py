"""Scoring and checks on hand-built manifests with known answers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jev_harness.core.check import Severity
from jev_harness.modules.reviewer_eval import checks, service
from jev_harness.modules.reviewer_eval.model import (
    Manifest,
    PRRecord,
    ReviewComment,
    TargetHunk,
)
from jev_harness.modules.reviewer_eval.score import hits_target, score_manifest

T0 = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)


def bug(n: int, *, path: str = "src/calc.py", lang: str = "py", directory: str = "src") -> PRRecord:
    return PRRecord(
        id=f"bug-{n}", kind="bug", fix_sha=f"{n:040x}", base_branch="b", head_branch="h",
        title="t", number=n, created_at=T0,
        targets=[TargetHunk(path=path, new_ranges=[(10, 12)], old_ranges=[(40, 40)])],
        languages=[lang], directories=[directory],
    )  # fmt: skip


def control(n: int) -> PRRecord:
    return PRRecord(
        id=f"control-{n}", kind="control", fix_sha=f"{n:040x}", base_branch="b", head_branch="h",
        title="t", number=100 + n, created_at=T0,
    )  # fmt: skip


def comment(
    reviewer: str, pr: PRRecord, *, line: int | None = 11, path: str | None = "src/calc.py",
    delay: int = 60, side: str = "RIGHT", start: int | None = None, kind: str = "inline",
) -> ReviewComment:  # fmt: skip
    return ReviewComment(
        reviewer=reviewer, pr_id=pr.id, kind=kind, body="x",  # type: ignore[arg-type]
        path=path, line=line, start_line=start, side=side,  # type: ignore[arg-type]
        created_at=T0 + timedelta(seconds=delay),
    )  # fmt: skip


def test_location_matching_rules() -> None:
    pr = bug(1)
    assert hits_target(comment("r", pr, line=10), pr)
    assert hits_target(comment("r", pr, line=12), pr)
    assert hits_target(comment("r", pr, line=14), pr)  # within the 2-line slack
    assert not hits_target(comment("r", pr, line=15), pr)
    assert hits_target(comment("r", pr, line=15), pr, slack=3)
    assert hits_target(comment("r", pr, line=20, start=11), pr)  # a range that overlaps
    assert not hits_target(comment("r", pr, line=11, path="src/other.py"), pr)
    assert hits_target(comment("r", pr, line=40, side="LEFT"), pr)  # deleted-line side
    assert not hits_target(comment("r", pr, line=40, side="RIGHT"), pr)
    assert not hits_target(comment("r", pr, kind="summary", line=None, path=None), pr)
    assert not hits_target(comment("r", control(1), line=11), control(1))  # controls hold no bug


def manifest() -> Manifest:
    prs = [bug(1), bug(2, path="web/app.ts", lang="ts", directory="web"), control(1), control(2)]
    prs[1].targets = [TargetHunk(path="web/app.ts", new_ranges=[(5, 5)])]
    comments = [
        # "precise" catches both bugs with one comment each and says nothing on the controls.
        comment("precise", prs[0], delay=30),
        comment("precise", prs[1], line=5, path="web/app.ts", delay=90),
        # "noisy" catches one bug, and talks a lot elsewhere.
        comment("noisy", prs[0], delay=600),
        comment("noisy", prs[0], line=200, delay=610),
        comment("noisy", prs[1], line=99, path="web/app.ts", delay=620),
        comment("noisy", prs[2], line=1, delay=630),
        comment("noisy", prs[3], line=1, delay=640),
        comment("noisy", prs[3], line=2, delay=650),
        comment("noisy", prs[2], kind="summary", line=None, path=None, delay=100),
    ]
    return Manifest(
        target_repo="acme/sandbox", source_repo="x", prs=prs, comments=comments,
        expected_reviewers=["silent"],
    )  # fmt: skip


def test_scores_have_the_exact_counts_the_comments_imply() -> None:
    scores = score_manifest(manifest())
    precise, noisy, silent = scores["precise"], scores["noisy"], scores["silent"]

    assert (precise.caught, precise.bug_prs) == (2, 2)
    assert precise.recall.estimate == 1.0
    assert (precise.true_positive_comments, precise.noise_comments) == (2, 0)
    assert precise.noise_per_pr == 0
    assert precise.false_alarm_rate is not None
    assert precise.false_alarm_rate.estimate == 0.0
    assert precise.median_latency_seconds == 60  # median of 30s and 90s
    assert precise.by_language == {"py": [1, 1], "ts": [1, 1]}
    assert precise.by_directory == {"src": [1, 1], "web": [1, 1]}

    assert (noisy.caught, noisy.bug_prs) == (1, 2)
    assert (noisy.true_positive_comments, noisy.noise_comments) == (1, 5)
    assert noisy.noise_per_pr == pytest.approx(5 / 4)
    assert (noisy.controls_flagged, noisy.controls) == (2, 2)  # both controls got inline comments
    assert noisy.summary_comments == 1
    assert noisy.by_language == {"py": [1, 1], "ts": [0, 1]}
    assert noisy.precision is not None
    assert noisy.precision.estimate == pytest.approx(1 / 6)

    assert (silent.caught, silent.prs_with_any_comment, silent.bug_prs) == (0, 0, 2)
    assert silent.median_latency_seconds is None
    assert silent.precision is None


def test_reviewer_filter_and_case_insensitive_names() -> None:
    m = manifest()
    only = score_manifest(m, reviewers=["PRECISE"])
    assert list(only) == ["PRECISE"]
    assert only["PRECISE"].caught == 2


def test_report_flags_the_weak_reviewers_and_not_the_precise_one() -> None:
    m = manifest()
    report = service.build_report(
        m, service.ReportOptions(min_prs=2, recall_floor=0.9, noise_ceiling=1.0)
    )
    by_rule: dict[str, set[str]] = {}
    for finding in report.findings:
        by_rule.setdefault(finding.rule_id, set()).add(finding.location or "")
    assert by_rule["reviewer.noise_above_ceiling"] == {"noisy"}
    assert "reviewer.latency_exceeded" not in by_rule
    assert "precise" not in {loc for locs in by_rule.values() for loc in locs}
    assert report.metrics["recall.precise"].startswith("2/2 bugs")
    assert report.metrics["recall_by_language.noisy.ts"] == "0/1"
    assert report.metrics["median_time_to_first_comment_s.silent"] == "no comments"
    assert report.metrics["coverage.silent"].startswith("0/4")


def many_bug_manifest(reviewer_catches: int, total: int = 10) -> Manifest:
    prs = [bug(i + 1) for i in range(total)] + [control(1)]
    comments = [comment("bot", prs[i]) for i in range(reviewer_catches)]
    return Manifest(
        target_repo="acme/sandbox",
        source_repo="x",
        prs=prs,
        comments=comments,
        expected_reviewers=["bot"],
    )


def test_a_reviewer_that_catches_everything_with_enough_trials_produces_no_findings() -> None:
    """Must-not-flag: 10 of 10 caught, nothing on the control PR, answered quickly."""
    report = service.build_report(many_bug_manifest(10))
    assert report.findings == []
    assert report.metrics["recall.bot"].startswith("10/10 bugs")


def test_recall_below_the_floor_needs_credible_evidence() -> None:
    low = service.build_report(many_bug_manifest(0))
    flagged = [f for f in low.findings if f.rule_id == "reviewer.recall_below_floor"]
    assert [f.location for f in flagged] == ["bot"]
    assert flagged[0].severity is Severity.flag
    assert "0 of 10" in flagged[0].message
    # 4 of 10 has an interval that still reaches the 50% floor, so it is not condemned.
    middling = service.build_report(many_bug_manifest(4))
    assert not [f for f in middling.findings if f.rule_id == "reviewer.recall_below_floor"]
    # Too few bugs: not enough to judge recall at all.
    tiny = service.build_report(many_bug_manifest(0, total=2))
    assert not [f for f in tiny.findings if f.rule_id == "reviewer.recall_below_floor"]


def test_insufficient_trials_and_untouched_bugs_are_reported() -> None:
    report = service.build_report(many_bug_manifest(3, total=5))
    ids = {f.rule_id for f in report.findings}
    assert "reviewer.insufficient_trials" in ids
    untouched = [f for f in report.findings if f.rule_id == "reviewer.bug_hunk_untouched"]
    assert sorted(f.location or "" for f in untouched) == ["bug-4", "bug-5"]
    assert all(f.severity is Severity.info for f in untouched)


def test_latency_check_uses_the_median_and_the_ceiling() -> None:
    m = many_bug_manifest(10)
    for c in m.comments:
        c.created_at = T0 + timedelta(seconds=2000)
    findings = service.build_report(m).findings
    slow = [f for f in findings if f.rule_id == "reviewer.latency_exceeded"]
    assert [f.location for f in slow] == ["bot"]
    assert slow[0].evidence["median_seconds"] == 2000


def test_report_states_its_limits_and_empty_manifests_say_nothing_was_collected() -> None:
    empty = Manifest(target_repo="acme/sandbox", source_repo="x", prs=[bug(1)])
    report = service.build_report(empty)
    text = "\n".join(report.limitations)
    assert "No comments have been collected yet" in text
    assert "proves the reviewer looked there" in text
    assert "one sample" in text
    assert "no reviewer output collected yet" in report.summary


def test_rule_ids_are_stable_and_namespaced() -> None:
    assert [c.rule_id for c in checks.default_checks()] == [
        "reviewer.recall_below_floor",
        "reviewer.noise_above_ceiling",
        "reviewer.latency_exceeded",
        "reviewer.insufficient_trials",
        "reviewer.bug_hunk_untouched",
    ]
