"""Collect review comments from GitHub and from local reviewer commands."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from jev_harness.core.errors import ConfigError
from jev_harness.modules.reviewer_eval.github import GitHubClient
from jev_harness.modules.reviewer_eval.model import Manifest, PRRecord, ReviewComment

LOCAL_TIMEOUT_SECONDS = 600
#: Reviewer apps post progress and quota notices as PR comments. They are not reviews: they never
#: count as an answer, a catch, coverage, or latency, and they are reported so a reader knows why a
#: reviewer is missing (seen live: CodeRabbit's "Review limit reached" and "Currently processing").
STATUS_PATTERN = re.compile(
    r"rate.?limit|review limit reached|currently processing|review in progress|"
    r"review (?:is )?paused|reviews? (?:are )?(?:paused|skipped)|action not completed",
    re.IGNORECASE,
)
LINE_COMMENT = re.compile(r"^(?P<path>[^:\s]+):(?P<line>\d+):\s*(?P<body>.+)$")
SleepFn = Callable[[float], Awaitable[None]]


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _login(item: dict[str, Any]) -> str | None:
    user = item.get("user")
    return str(user["login"]) if isinstance(user, dict) and user.get("login") else None


def is_status_notice(body: str) -> bool:
    return bool(STATUS_PATTERN.search(body))


def status_line(body: str) -> str:
    """The first readable line of a status notice, for the report."""
    text = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    for line in text.splitlines():
        cleaned = re.sub(r"^[\s>#*]*(?:\[![A-Z]+\]\s*)?", "", line).strip(" *#>")
        if cleaned and STATUS_PATTERN.search(cleaned):
            return cleaned[:160]
    return "status notice"


def normalise_github(
    pr: PRRecord,
    review_comments: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    *,
    exclude: str | None,
) -> list[ReviewComment]:
    """Turn the three GitHub comment endpoints into one list of :class:`ReviewComment`."""
    out: list[ReviewComment] = []
    for item in review_comments:
        login = _login(item)
        if login is None or login == exclude:
            continue
        line = item.get("line") or item.get("original_line")
        out.append(
            ReviewComment(
                reviewer=login,
                pr_id=pr.id,
                kind="inline" if item.get("path") and line else "summary",
                body=str(item.get("body", "")),
                path=item.get("path"),
                line=int(line) if line else None,
                start_line=int(item["start_line"]) if item.get("start_line") else None,
                side="LEFT" if item.get("side") == "LEFT" else "RIGHT",
                created_at=_timestamp(item.get("created_at")),
                origin="inline",
            )
        )
    for item in reviews:
        login = _login(item)
        body = str(item.get("body") or "").strip()
        if login is None or login == exclude or not body:
            continue
        out.append(
            ReviewComment(
                reviewer=login,
                pr_id=pr.id,
                kind="summary",
                body=body,
                created_at=_timestamp(item.get("submitted_at")),
                origin="review",
                status=is_status_notice(body),
            )
        )
    for item in issue_comments:
        login = _login(item)
        if login is None or login == exclude:
            continue
        out.append(
            ReviewComment(
                reviewer=login,
                pr_id=pr.id,
                kind="summary",
                body=str(item.get("body", "")),
                created_at=_timestamp(item.get("created_at")),
                origin="issue",
                status=is_status_notice(str(item.get("body", ""))),
            )
        )
    return out


async def fetch_all(
    manifest: Manifest, client: GitHubClient, *, exclude: str | None
) -> list[ReviewComment]:
    comments: list[ReviewComment] = []
    for pr in manifest.prs:
        if pr.number is None:
            continue
        review_comments, reviews, issue_comments = await asyncio.gather(
            client.review_comments(manifest.target_repo, pr.number),
            client.reviews(manifest.target_repo, pr.number),
            client.issue_comments(manifest.target_repo, pr.number),
        )
        comments.extend(
            normalise_github(pr, review_comments, reviews, issue_comments, exclude=exclude)
        )
    return comments


def _filter(comments: list[ReviewComment], reviewers: list[str]) -> list[ReviewComment]:
    if not reviewers:
        return comments
    wanted = {r.lower() for r in reviewers}
    return [c for c in comments if c.reviewer.lower() in wanted]


def _complete(manifest: Manifest, comments: list[ReviewComment], reviewers: list[str]) -> bool:
    if not reviewers:
        return False
    # Only review content answers a PR: an inline comment or a review body. A PR conversation
    # comment may be a bot's summary or "still processing" note, so waiting continues.
    seen = {
        (c.reviewer.lower(), c.pr_id)
        for c in comments
        if not c.status and c.origin in ("inline", "review")
    }
    return all((r.lower(), pr.id) in seen for r in reviewers for pr in manifest.prs if pr.number)


async def collect_github(
    manifest: Manifest,
    client: GitHubClient,
    *,
    reviewers: list[str],
    wait_seconds: float = 0.0,
    interval_seconds: float = 30.0,
    settle_seconds: float = 120.0,
    sleep: SleepFn = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Manifest:
    """Poll until every expected reviewer has answered every PR, nothing new arrives, or time is up.

    Always fetches at least once. Missing answers are reported, never invented.
    """
    login = await client.authenticated_login()
    started = clock()
    last_change = started
    previous = -1
    comments: list[ReviewComment] = []
    notes: list[str] = []
    while True:
        comments = _filter(await fetch_all(manifest, client, exclude=login), reviewers)
        now = clock()
        if len(comments) != previous:
            previous, last_change = len(comments), now
        if _complete(manifest, comments, reviewers):
            notes.append("every expected reviewer answered every pull request")
            break
        if now - started >= wait_seconds:
            if wait_seconds > 0:
                notes.append(f"stopped waiting after {wait_seconds:g}s")
            break
        if comments and now - last_change >= settle_seconds:
            notes.append(f"no new comments for {settle_seconds:g}s; stopped waiting")
            break
        await sleep(interval_seconds)

    updated = manifest.model_copy(deep=True)
    local = [c for c in manifest.comments if c.reviewer.startswith("local:")]
    updated.comments = [*comments, *local]
    updated.expected_reviewers = sorted(set(manifest.expected_reviewers) | set(reviewers))
    numbers = {pr.id: pr.number for pr in manifest.prs}
    for comment in comments:
        if comment.status:
            note = (
                f"PR #{numbers.get(comment.pr_id)} ({comment.pr_id}): {comment.reviewer} posted a "
                f"status notice, not a review: {status_line(comment.body)!r}"
            )
            if note not in notes:
                notes.append(note)
    updated.collection_notes = [*manifest.collection_notes, *notes]
    return updated


def parse_local_output(
    reviewer: str, pr: PRRecord, output: str, created_at: datetime
) -> list[ReviewComment]:
    """A local reviewer prints a JSON array of comments or ``path:line: text`` lines."""
    text = output.strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, list):
        comments = []
        for item in loaded:
            if not isinstance(item, dict) or not item.get("body"):
                continue
            has_location = bool(item.get("path")) and bool(item.get("line"))
            comments.append(
                ReviewComment(
                    reviewer=reviewer,
                    pr_id=pr.id,
                    kind="inline" if has_location else "summary",
                    body=str(item["body"]),
                    path=item.get("path") if has_location else None,
                    line=int(item["line"]) if has_location else None,
                    start_line=int(item["start_line"]) if item.get("start_line") else None,
                    side="LEFT" if item.get("side") == "LEFT" else "RIGHT",
                    created_at=created_at,
                    origin="local",
                )
            )
        return comments
    comments = []
    leftovers: list[str] = []
    for line in text.splitlines():
        match = LINE_COMMENT.match(line.strip())
        if match:
            comments.append(
                ReviewComment(
                    reviewer=reviewer,
                    pr_id=pr.id,
                    kind="inline",
                    body=match.group("body"),
                    path=match.group("path"),
                    line=int(match.group("line")),
                    created_at=created_at,
                )
            )
        elif line.strip():
            leftovers.append(line.strip())
    if not comments and leftovers:
        comments.append(
            ReviewComment(
                reviewer=reviewer,
                pr_id=pr.id,
                kind="summary",
                body="\n".join(leftovers),
                created_at=created_at,
            )
        )
    return comments


def parse_local_spec(spec: str) -> tuple[str, list[str]]:
    name, sep, command = spec.partition("=")
    if not sep or not name.strip() or not command.strip():
        raise ConfigError(f"--local-reviewer must look like NAME=COMMAND, got {spec!r}")
    argv = shlex.split(command)
    if not any("{diff}" in part for part in argv):
        raise ConfigError(f"--local-reviewer {name!r}: the command must contain {{diff}}")
    return f"local:{name.strip()}", argv


async def run_local_reviewers(
    manifest: Manifest,
    client: GitHubClient,
    specs: list[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Manifest:
    """Run each local reviewer command on each PR's diff (fetched from GitHub)."""
    updated = manifest.model_copy(deep=True)
    for spec in specs:
        reviewer, argv = parse_local_spec(spec)
        updated.comments = [c for c in updated.comments if c.reviewer != reviewer]
        for pr in manifest.prs:
            if pr.number is None:
                continue
            diff = await client.pull_diff(manifest.target_repo, pr.number)
            with tempfile.TemporaryDirectory(prefix="jev-review-") as directory:
                diff_path = Path(directory) / "pr.diff"
                diff_path.write_text(diff, encoding="utf-8")
                command = [part.replace("{diff}", str(diff_path)) for part in argv]
                started = time.monotonic()
                try:
                    completed = runner(
                        command, capture_output=True, text=True, timeout=LOCAL_TIMEOUT_SECONDS,
                        check=False,
                    )  # fmt: skip
                except (OSError, subprocess.TimeoutExpired) as exc:
                    updated.collection_notes.append(f"{reviewer} on {pr.id}: could not run ({exc})")
                    continue
                elapsed = time.monotonic() - started
            if completed.returncode != 0:
                updated.collection_notes.append(
                    f"{reviewer} on {pr.id}: exited {completed.returncode}; no comments recorded"
                )
                continue
            created = (pr.created_at or datetime.now(UTC)) + timedelta(seconds=elapsed)
            updated.comments.extend(
                c.model_copy(update={"origin": "local"})
                for c in parse_local_output(reviewer, pr, completed.stdout, created)
            )
        if reviewer not in updated.expected_reviewers:
            updated.expected_reviewers.append(reviewer)
    return updated
