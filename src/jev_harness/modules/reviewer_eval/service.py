"""Compose scoring, checks, and the report for ``reviewers report``; plus cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jev_harness.core.check import Eval
from jev_harness.core.report import Report
from jev_harness.modules.reviewer_eval import checks
from jev_harness.modules.reviewer_eval.github import GitHubClient, GitHubError
from jev_harness.modules.reviewer_eval.model import Manifest
from jev_harness.modules.reviewer_eval.score import DEFAULT_SLACK, score_manifest


class ReviewerEval(Eval):
    module = checks.MODULE


@dataclass(slots=True)
class ReportOptions:
    slack: int = DEFAULT_SLACK
    reviewers: list[str] | None = None
    recall_floor: float = checks.DEFAULT_RECALL_FLOOR
    noise_ceiling: float = checks.DEFAULT_NOISE_CEILING
    latency_ceiling: float = checks.DEFAULT_LATENCY_CEILING
    min_prs: int = checks.DEFAULT_MIN_PRS
    min_judge_confidence: float = 0.7


def build_report(manifest: Manifest, options: ReportOptions | None = None) -> Report:
    options = options or ReportOptions()
    scores = score_manifest(
        manifest,
        slack=options.slack,
        reviewers=options.reviewers,
        min_judge_confidence=options.min_judge_confidence,
    )
    evaluator = ReviewerEval(
        checks.default_checks(
            recall_floor=options.recall_floor,
            noise_ceiling=options.noise_ceiling,
            latency_ceiling=options.latency_ceiling,
            min_prs=options.min_prs,
            min_judge_confidence=options.min_judge_confidence,
        )
    )
    findings = evaluator.run_checks(checks.Subject(manifest, scores))

    bug_prs = sum(1 for p in manifest.prs if p.kind == "bug")
    controls = sum(1 for p in manifest.prs if p.kind == "control")
    metrics: dict[str, Any] = {
        "bug_prs": bug_prs,
        "control_prs": controls,
        "reviewers": ", ".join(scores) or "none",
        "location_slack_lines": options.slack,
    }
    for name, score in scores.items():
        metrics[f"recall.{name}"] = f"{score.caught}/{score.bug_prs} bugs; {score.recall}"
        if score.precision is not None:
            metrics[f"precision.{name}"] = (
                f"{score.true_positive_comments}/"
                f"{score.true_positive_comments + score.noise_comments} located comments; "
                f"{score.precision}"
            )
        metrics[f"noise_per_pr.{name}"] = round(score.noise_per_pr, 2)
        if score.false_alarm_rate is not None:
            metrics[f"false_alarm_rate.{name}"] = (
                f"{score.controls_flagged}/{score.controls} control PRs got an inline comment; "
                f"{score.false_alarm_rate}"
            )
        if score.confirmed_recall is not None:
            metrics[f"confirmed_recall.{name}"] = (
                f"{score.confirmed}/{score.bug_prs} bugs; {score.confirmed_recall} "
                f"({score.rejected} rejected, "
                f"{sum(1 for v in score.judge_status.values() if v == 'unclear')} unclear)"
            )
        median = score.median_latency_seconds
        metrics[f"median_time_to_first_comment_s.{name}"] = (
            "no comments" if median is None else round(median, 1)
        )
        metrics[f"coverage.{name}"] = (
            f"{score.prs_with_any_comment}/{score.prs_total} PRs got any comment from it"
        )
        metrics[f"summary_comments.{name}"] = score.summary_comments
        for language, (k, n) in sorted(score.by_language.items()):
            metrics[f"recall_by_language.{name}.{language}"] = f"{k}/{n}"
        for directory, (k, n) in sorted(score.by_directory.items()):
            metrics[f"recall_by_directory.{name}.{directory}"] = f"{k}/{n}"

    limitations = [
        "A comment catches a bug when it is an inline comment on the re-introduced lines (within "
        f"{options.slack} line(s)). That proves the reviewer looked there, not that it described "
        "the bug correctly. Run with --judge to have a model check the description; that adds "
        "confirmed_recall and never changes what counts as caught.",
        "Summary comments (PR descriptions, review bodies, issue comments) carry no location, so "
        "they count as neither a catch nor noise; they are counted separately.",
        "Noise is every inline comment that is off the bug on a bug PR, or anywhere on a control "
        "PR. A control PR is the real fix applied forward, so a good reviewer can still have "
        "something true to say about it.",
        "A reviewer that did not answer a PR is counted as not catching its bug; the coverage "
        "metric shows how many PRs it answered at all.",
        "Each reviewer saw each bug once. Reviewers are not deterministic, so one run is one "
        "sample, not a measurement of a rate.",
        "Pull requests contain a snapshot of the files at each commit without history, which can "
        "give a reviewer less context than it would have on your real repository.",
        *manifest.collection_notes,
    ]
    if manifest.confirmations:
        judges = sorted({c.judge for c in manifest.confirmations})
        limitations.append(
            f"Judge ({', '.join(judges)}) opinions on located comments are advisory: recall is "
            f"by location, confirmed_recall counts catches the judge agreed with at "
            f"{options.min_judge_confidence:.0%} confidence or more, and an answer below that "
            "(or a failed call) is reported as unclear, not as a confirmation."
        )
    if not manifest.comments:
        limitations.append(
            "No comments have been collected yet; run `reviewers collect` after the reviewers "
            "have had time to answer."
        )
    summary = (
        f"{bug_prs} planted bug(s) and {controls} control PR(s) on {manifest.target_repo}; "
        + (
            ", ".join(f"{n} caught {s.caught}/{s.bug_prs}" for n, s in scores.items())
            if scores
            else "no reviewer output collected yet"
        )
        + "."
    )
    return evaluator.build_report(
        title="reviewers: which AI reviewer catches real bugs in your code",
        summary=summary,
        findings=findings,
        metrics=metrics,
        meta={
            "target_repo": manifest.target_repo,
            "bug_prs": bug_prs,
            "control_prs": controls,
            "reviewers": ",".join(scores),
        },
        limitations=limitations,
    ).model_copy(
        update={
            "headline": [
                "bug_prs",
                "control_prs",
                "recall.*",
                "confirmed_recall.*",
                "precision.*",
                "noise_per_pr.*",
                "median_time_to_first_comment_s.*",
                "coverage.*",
            ]
        }
    )


async def cleanup(manifest: Manifest, client: GitHubClient) -> tuple[Manifest, list[str]]:
    """Close every recorded PR and delete its branches on the manifest's own target repo."""
    updated = manifest.model_copy(deep=True)
    actions: list[str] = []
    default_branch = str((await client.get_repo(updated.target_repo)).get("default_branch") or "")
    for pr in updated.prs:
        if pr.closed:
            continue
        if pr.number is not None:
            await client.close_pull(updated.target_repo, pr.number)
            actions.append(f"closed #{pr.number} ({pr.id})")
        for branch in (pr.head_branch, pr.base_branch):
            if branch == default_branch:
                # Pushing to an empty repository makes the first branch the default, and GitHub
                # refuses to delete a default branch.
                actions.append(
                    f"kept: {branch} is the repository's default branch, which GitHub will not "
                    "delete; set another default branch in the repository settings, then delete "
                    "it there (create the sandbox with a README next time to avoid this)"
                )
                continue
            try:
                deleted = await client.delete_branch(updated.target_repo, branch)
            except GitHubError as exc:
                actions.append(f"could not delete {branch}: {exc}")
                continue
            actions.append(f"{'deleted' if deleted else 'already gone'}: {branch}")
        pr.closed = True
    return updated, actions
