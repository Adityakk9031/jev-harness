"""Build bug-reintroducing and control pull requests in a sandbox repository.

Nothing here touches the source repository's refs. Each pull request is built in a throwaway
repository from a file snapshot (no history), pushed only to the named sandbox, and only after the
caller has confirmed the exact list of refs.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from jev_harness.core.errors import ConfigError, HarnessError
from jev_harness.modules.reviewer_eval import gitops
from jev_harness.modules.reviewer_eval.github import GitHubClient, validate_slug
from jev_harness.modules.reviewer_eval.model import Manifest, PRKind, PRRecord

SNAPSHOT_MESSAGE = "Add project snapshot"
MAX_DIFF_EXCERPT = 4000
DEFAULT_MAX_CONTROLS = 3


class ConfirmationDeclinedError(HarnessError):
    """The person did not confirm the push, so nothing was pushed."""


@dataclass(slots=True)
class PlannedPR:
    id: str
    kind: PRKind
    fix_sha: str
    base_branch: str
    head_branch: str
    title: str
    body: str
    files: list[str]

    @property
    def refs(self) -> list[str]:
        return [self.base_branch, self.head_branch]


@dataclass(slots=True)
class BuildPlan:
    source: Path
    target_repo: str
    git_url: str
    run_tag: str
    prs: list[PlannedPR] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    #: Comments posted on every PR to wake reviewer apps (for example "@coderabbitai review").
    trigger_comments: list[str] = field(default_factory=list)
    #: Logins requested as reviewers on every PR.
    request_reviewers: list[str] = field(default_factory=list)

    def refs(self) -> list[str]:
        return [ref for pr in self.prs for ref in pr.refs]

    def confirmation_lines(self) -> list[str]:
        lines = [
            f"target repository:  {self.target_repo}",
            f"git remote URL:     {self.git_url}",
            f"pull requests:      {sum(p.kind == 'bug' for p in self.prs)} bug, "
            f"{sum(p.kind == 'control' for p in self.prs)} control",
            f"branches to push ({len(self.refs())}):",
            *[f"  {ref}" for ref in self.refs()],
            "",
            "What this does:",
            "  * pushes a snapshot of the tracked files at each fix commit (no history) and",
            "    opens pull requests on the target repository only;",
            "  * never pushes to or changes the source repository;",
            "  * every reviewer app installed on the target repository will read this code",
            "    and may send it to a third party.",
        ]
        if self.excludes:
            lines.append(f"  * paths removed from every snapshot: {', '.join(self.excludes)}")
        lines.extend(f"  * posts this comment on every PR: {t!r}" for t in self.trigger_comments)
        if self.request_reviewers:
            lines.append(f"  * requests review from: {', '.join(self.request_reviewers)}")
        return lines


def neutral_title(files: list[str]) -> str:
    stem = PurePosixPath(files[0]).stem.replace("_", " ")
    return f"Update {stem} handling"


def neutral_body(files: list[str]) -> str:
    return "Adjusts behaviour in " + ", ".join(f"`{f}`" for f in files) + "."


def plan_build(
    source: Path,
    target_repo: str,
    fixes: list[str],
    *,
    controls: int | None = None,
    git_url: str | None = None,
    excludes: list[str] | None = None,
    trigger_comments: list[str] | None = None,
    request_reviewers: list[str] | None = None,
) -> BuildPlan:
    validate_slug(target_repo)
    repo = gitops.require_repo(source)
    if target_repo.lower() in gitops.remote_slugs(repo):
        raise ConfigError(
            f"{target_repo} is a remote of the source repository. Reviewers must run on a separate "
            "sandbox repository; this tool never pushes to the repository it reads."
        )
    if not fixes:
        raise ConfigError("no fix commits chosen: pass --fix SHA (repeatable) or --candidates FILE")
    run_tag = secrets.token_hex(3)
    plan = BuildPlan(
        source=repo,
        target_repo=target_repo,
        git_url=git_url or f"https://github.com/{target_repo}.git",
        run_tag=run_tag,
        excludes=excludes or [],
        trigger_comments=trigger_comments or [],
        request_reviewers=request_reviewers or [],
    )
    resolved = [gitops.resolve_commit(repo, ref) for ref in fixes]
    for sha in resolved:
        _patch, files = gitops.fix_source_patch(repo, sha)
        short = sha[:8]
        plan.prs.append(
            PlannedPR(
                id=f"bug-{short}",
                kind="bug",
                fix_sha=sha,
                base_branch=f"jev-base/{run_tag}-{short}",
                head_branch=f"jev-bug/{run_tag}-{short}",
                title=neutral_title(files),
                body=neutral_body(files),
                files=files,
            )
        )
    wanted = min(len(resolved), DEFAULT_MAX_CONTROLS) if controls is None else controls
    for sha in resolved[: max(0, wanted)]:
        if not gitops.git(repo, "rev-list", "--parents", "-n1", sha).split()[1:]:
            continue  # a root commit has no parent snapshot to start from
        _patch, files = gitops.fix_source_patch(repo, sha)
        short = sha[:8]
        plan.prs.append(
            PlannedPR(
                id=f"control-{short}",
                kind="control",
                fix_sha=sha,
                base_branch=f"jev-cbase/{run_tag}-{short}",
                head_branch=f"jev-ctl/{run_tag}-{short}",
                title=neutral_title(files),
                body=neutral_body(files),
                files=files,
            )
        )
    return plan


def _push(cwd: Path, url: str, refs: list[str], token: str | None) -> None:
    env: dict[str, str] = {}
    if token:
        # Passed through the environment, not argv, so the token is not visible in `ps`.
        env = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraheader",
            "GIT_CONFIG_VALUE_0": gitops.basic_auth_header(token),
        }
    try:
        gitops.git(cwd, "push", "--no-verify", url, *refs, env=env)
    except gitops.GitError as exc:
        message = str(exc)
        if token:
            message = message.replace(token, "<token>")
        raise gitops.GitError(message) from None


def _remove_excluded(root: Path, patterns: list[str]) -> None:
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if ".git" in path.parts:
                continue
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                import shutil

                shutil.rmtree(path, ignore_errors=True)


def build_branches(plan: BuildPlan, pr: PlannedPR, token: str | None) -> PRRecord:
    """Create and push the two branches for one PR, returning its record (without a PR number)."""
    source = plan.source
    patch, files = gitops.fix_source_patch(source, pr.fix_sha)
    base_ref = pr.fix_sha if pr.kind == "bug" else f"{pr.fix_sha}^"
    with gitops.snapshot_repo(source, base_ref) as work:
        _remove_excluded(work, plan.excludes)
        gitops.init_repo(work)
        gitops.commit_all(work, SNAPSHOT_MESSAGE, branch=pr.base_branch)
        gitops.apply_patch(work, patch, reverse=(pr.kind == "bug"))
        gitops.commit_all(work, pr.title, branch=pr.head_branch)
        diff = gitops.git(work, "diff", pr.base_branch, pr.head_branch)
        targets = gitops.target_hunks(diff) if pr.kind == "bug" else []
        fix_subject = gitops.git(source, "log", "-1", "--format=%s", pr.fix_sha).strip()
        _push(work, plan.git_url, pr.refs, token)
    languages = sorted({PurePosixPath(f).suffix.lstrip(".") or "none" for f in files})
    directories = sorted(
        {(PurePosixPath(f).parts[0] if len(PurePosixPath(f).parts) > 1 else ".") for f in files}
    )
    return PRRecord(
        id=pr.id,
        kind=pr.kind,
        fix_sha=pr.fix_sha,
        base_branch=pr.base_branch,
        head_branch=pr.head_branch,
        title=pr.title,
        fix_subject=fix_subject,
        diff_excerpt=diff[:MAX_DIFF_EXCERPT] if pr.kind == "bug" else "",
        targets=targets,
        languages=languages,
        directories=directories,
    )


async def execute_build(
    plan: BuildPlan,
    client: GitHubClient,
    token: str | None,
    confirm: Callable[[list[str]], bool],
) -> Manifest:
    """Confirm, then push every branch and open every PR on the target. Raises if declined."""
    if not confirm(plan.confirmation_lines()):
        raise ConfirmationDeclinedError("not confirmed; nothing was pushed")
    await client.get_repo(plan.target_repo)  # fails early if the token cannot see the sandbox
    manifest = Manifest(
        target_repo=plan.target_repo,
        git_url=plan.git_url,
        source_repo=str(plan.source),
    )
    for pr in plan.prs:
        record = build_branches(plan, pr, token)
        manifest.prs.append(record)  # recorded before the API call, so cleanup can find branches
        created = await client.create_pull(
            plan.target_repo,
            title=pr.title,
            head=pr.head_branch,
            base=pr.base_branch,
            body=pr.body,
        )
        record.number = int(created["number"])
        record.url = str(created.get("html_url", ""))
        stamp = created.get("created_at")
        record.created_at = (
            datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if stamp
            else datetime.now(UTC)
        )
        # Many reviewer apps only review PRs into the default branch unless asked; these are
        # how to ask. A failure is recorded, not fatal: the PR already exists and must stay in
        # the manifest so cleanup can find it.
        for text in plan.trigger_comments:
            try:
                await client.comment(plan.target_repo, record.number, text)
            except HarnessError as exc:
                manifest.collection_notes.append(f"{pr.id}: trigger comment failed: {exc}")
        if plan.request_reviewers:
            try:
                await client.request_reviewers(
                    plan.target_repo, record.number, plan.request_reviewers
                )
            except HarnessError as exc:
                manifest.collection_notes.append(f"{pr.id}: review request failed: {exc}")
    return manifest


def input_confirm(
    lines: list[str], *, assume_yes: bool, read: Callable[[str], str] = input
) -> bool:
    """Show the push plan and ask. Non-interactive runs must opt in with ``--yes``."""
    for line in lines:
        print(line)  # noqa: T201 - this is the confirmation screen
    if assume_yes:
        print("--yes given: continuing.")  # noqa: T201
        return True
    if not os.isatty(0) and read is input:
        return False
    return read("Type 'yes' to push these branches and open these pull requests: ").strip() == "yes"
