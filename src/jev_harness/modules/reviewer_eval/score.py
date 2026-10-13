"""Score reviewers by where their comments land, using only the diff and the comment locations."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from jev_harness.core.stats import Interval, wilson
from jev_harness.modules.reviewer_eval.model import Manifest, PRRecord, Range, ReviewComment

DEFAULT_SLACK = 2


def _intersects(ranges: list[Range], low: int, high: int, slack: int) -> bool:
    return any(low <= end + slack and high >= start - slack for start, end in ranges)


def hits_target(comment: ReviewComment, pr: PRRecord, slack: int = DEFAULT_SLACK) -> bool:
    """Whether an inline comment points at the re-introduced lines of a bug PR."""
    if pr.kind != "bug" or comment.kind != "inline" or comment.path is None or comment.line is None:
        return False
    low = min(comment.start_line or comment.line, comment.line)
    high = max(comment.start_line or comment.line, comment.line)
    for target in pr.targets:
        if target.path != comment.path:
            continue
        ranges = target.old_ranges if comment.side == "LEFT" else target.new_ranges
        if _intersects(ranges, low, high, slack):
            return True
    return False


def pair_status(manifest: Manifest, reviewer: str, pr_id: str, min_confidence: float) -> str | None:
    """What the judge concluded about a reviewer's located comments on one bug PR.

    ``None`` means no judge was involved. Otherwise ``confirmed`` (a trusted answer said a
    comment describes the bug), ``rejected`` (trusted answers said they are about something
    else), or ``unclear`` (no trusted answer either way, including when the judge failed).
    """
    entries = [
        c
        for c in manifest.confirmations
        if c.reviewer.lower() == reviewer.lower() and c.pr_id == pr_id
    ]
    if not entries:
        return None
    trusted_entries = [c for c in entries if c.confidence >= min_confidence]
    if any(c.answer == "describes_bug" for c in trusted_entries):
        return "confirmed"
    if any(c.answer == "unrelated" for c in trusted_entries):
        return "rejected"
    return "unclear"


@dataclass(slots=True)
class ReviewerScore:
    reviewer: str
    bug_prs: int = 0
    caught: int = 0
    controls: int = 0
    controls_flagged: int = 0
    true_positive_comments: int = 0
    noise_comments: int = 0
    summary_comments: int = 0
    prs_with_any_comment: int = 0
    prs_total: int = 0
    latencies: list[float] = field(default_factory=list)
    caught_ids: set[str] = field(default_factory=set)
    #: Judge status per caught bug PR: confirmed, rejected, unclear. Empty when no judge ran.
    judge_status: dict[str, str] = field(default_factory=dict)
    by_language: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))
    by_directory: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))

    @property
    def recall(self) -> Interval:
        return wilson(self.caught, self.bug_prs)

    @property
    def precision(self) -> Interval | None:
        total = self.true_positive_comments + self.noise_comments
        return wilson(self.true_positive_comments, total) if total else None

    @property
    def confirmed(self) -> int:
        return sum(1 for v in self.judge_status.values() if v == "confirmed")

    @property
    def rejected(self) -> int:
        return sum(1 for v in self.judge_status.values() if v == "rejected")

    @property
    def confirmed_recall(self) -> Interval | None:
        return wilson(self.confirmed, self.bug_prs) if self.judge_status else None

    @property
    def false_alarm_rate(self) -> Interval | None:
        return wilson(self.controls_flagged, self.controls) if self.controls else None

    @property
    def noise_per_pr(self) -> float:
        return self.noise_comments / self.prs_total if self.prs_total else 0.0

    @property
    def median_latency_seconds(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    @property
    def coverage(self) -> Interval:
        return wilson(self.prs_with_any_comment, self.prs_total)


def score_manifest(
    manifest: Manifest,
    *,
    slack: int = DEFAULT_SLACK,
    reviewers: list[str] | None = None,
    min_judge_confidence: float = 0.7,
) -> dict[str, ReviewerScore]:
    names = sorted(reviewers or manifest.reviewers())
    wanted = {n.lower() for n in names}
    scores = {name: ReviewerScore(reviewer=name, prs_total=len(manifest.prs)) for name in names}
    by_pr_reviewer: dict[tuple[str, str], list[ReviewComment]] = defaultdict(list)
    for comment in manifest.comments:
        if comment.reviewer.lower() in wanted:
            canonical = next(n for n in names if n.lower() == comment.reviewer.lower())
            by_pr_reviewer[(canonical, comment.pr_id)].append(comment)

    for name, score in scores.items():
        for pr in manifest.prs:
            comments = by_pr_reviewer.get((name, pr.id), [])
            if comments:
                score.prs_with_any_comment += 1
                times = [c.created_at for c in comments if c.created_at is not None]
                if times and pr.created_at is not None:
                    score.latencies.append(max(0.0, (min(times) - pr.created_at).total_seconds()))
            inline = [c for c in comments if c.kind == "inline"]
            score.summary_comments += sum(1 for c in comments if c.kind == "summary")
            if pr.kind == "bug":
                hit = [c for c in inline if hits_target(c, pr)]
                score.bug_prs += 1
                score.true_positive_comments += len(hit)
                score.noise_comments += len(inline) - len(hit)
                caught = bool(hit)
                if caught:
                    score.caught += 1
                    score.caught_ids.add(pr.id)
                    status = pair_status(manifest, name, pr.id, min_judge_confidence)
                    if status is not None:
                        score.judge_status[pr.id] = status
                for language in pr.languages:
                    score.by_language[language][1] += 1
                    score.by_language[language][0] += int(caught)
                for directory in pr.directories:
                    score.by_directory[directory][1] += 1
                    score.by_directory[directory][0] += int(caught)
            else:
                score.controls += 1
                score.noise_comments += len(inline)
                score.controls_flagged += int(bool(inline))
    for name, score in scores.items():
        if any(c.reviewer.lower() == name.lower() for c in manifest.confirmations):
            # The judge was asked about this reviewer: a catch it gave no usable answer on is
            # unclear, never silently confirmed.
            for pr_id in score.caught_ids:
                score.judge_status.setdefault(pr_id, "unclear")
    return scores
