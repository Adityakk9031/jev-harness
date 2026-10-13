"""Git plumbing for ``reviewers``: mining fix commits and building bug-reintroducing commits.

Every call is an argv list (no shell). The source repository is only ever read; commits are made
in a throwaway repository created from a file snapshot, so no history, branch, or ref of the
user's repository is touched or pushed.
"""

from __future__ import annotations

import base64
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from unidiff.patch import PatchSet

from jev_harness.core.errors import HarnessError
from jev_harness.modules.reviewer_eval.model import Candidate, Range, TargetHunk

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".c", ".cc", ".cpp", ".h",
    ".hpp", ".cs", ".kt", ".swift", ".php", ".scala",
}  # fmt: skip
FIX_WORDS = re.compile(
    r"\b(fix(?:e[sd])?|bug|regress(?:ion)?|crash(?:es)?|error|hotfix|broken|incorrect|wrong|fail(?:s|ed|ure)?)\b",
    re.IGNORECASE,
)
ISSUE_LINK = re.compile(
    r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#\d+|\(#\d+\)", re.IGNORECASE
)
TEST_PARTS = {"test", "tests", "spec", "specs", "__tests__", "testing"}
BOT_IDENTITY = ("jev-harness", "jev-harness@users.noreply.invalid")
NEUTRAL_AUTHOR_ENV = {
    "GIT_AUTHOR_NAME": BOT_IDENTITY[0],
    "GIT_AUTHOR_EMAIL": BOT_IDENTITY[1],
    "GIT_COMMITTER_NAME": BOT_IDENTITY[0],
    "GIT_COMMITTER_EMAIL": BOT_IDENTITY[1],
}
GIT_TIMEOUT = 120


class GitError(HarnessError):
    """A git command failed."""


def git(
    cwd: Path,
    *args: str,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    import os

    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=False,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {GIT_TIMEOUT}s") from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:400]
        raise GitError(f"git {' '.join(args[:2])} failed: {detail}")
    return completed.stdout


def is_test_file(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if any(part.lower() in TEST_PARTS for part in pure.parts[:-1]):
        return True
    return bool(
        re.match(
            r"^(test_.*|.*_test\.\w+|.*\.test\.\w+|.*\.spec\.\w+|.*_spec\.\w+|conftest\.py)$", name
        )
    )


def is_source_file(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS and not is_test_file(path)


def require_repo(path: Path) -> Path:
    if not path.is_dir():
        raise GitError(f"not a directory: {path}")
    top = git(path, "rev-parse", "--show-toplevel").strip()
    return Path(top)


def remote_slugs(repo: Path) -> set[str]:
    """``owner/name`` for every remote of ``repo``, lower-cased, to refuse pushing to the source."""
    slugs: set[str] = set()
    for line in git(repo, "remote", "-v", check=False).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", parts[1])
        if match:
            slugs.add(match.group(1).lower())
    return slugs


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    added: int
    deleted: int


def numstat(repo: Path, sha: str) -> list[FileChange]:
    changes: list[FileChange] = []
    for line in git(repo, "show", "--numstat", "--format=", sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        deleted = int(parts[1]) if parts[1].isdigit() else 0
        changes.append(FileChange(parts[2], added, deleted))
    return changes


def mine_candidates(
    repo: Path,
    *,
    limit: int = 200,
    max_lines: int = 40,
    max_source_files: int = 3,
    since: str | None = None,
) -> list[Candidate]:
    """Rank commits that look like small bug fixes with tests. Rules only; a human confirms."""
    args = ["log", "--no-merges", f"-n{limit}", "--format=%H%x1f%aI%x1f%s%x1f%b%x1e"]
    if since:
        args.append(f"--since={since}")
    raw = git(repo, *args)
    candidates: list[Candidate] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, date, subject, body = ([*record.split("\x1f"), "", "", "", ""])[:4]
        if subject.lower().startswith("revert"):
            continue
        if not git(repo, "rev-list", "--parents", "-n1", sha.strip()).split()[1:]:
            continue  # a root commit has no earlier state, so it cannot be a fix
        message = f"{subject}\n{body}"
        changes = numstat(repo, sha.strip())
        source = [c for c in changes if is_source_file(c.path)]
        tests = [c for c in changes if is_test_file(c.path)]
        if not source or not tests or len(source) > max_source_files:
            continue
        lines = sum(c.added + c.deleted for c in source)
        if lines == 0 or lines > max_lines:
            continue
        reasons = ["touches source and tests"]
        score = 3
        if FIX_WORDS.search(message):
            reasons.append("message mentions a fix")
            score += 2
        if ISSUE_LINK.search(message):
            reasons.append("links an issue")
            score += 2
        if lines <= max_lines // 4:
            reasons.append(f"small change ({lines} lines)")
            score += 2
        elif lines <= max_lines // 2:
            reasons.append(f"modest change ({lines} lines)")
            score += 1
        if score == 3:
            continue  # source + tests alone is not evidence of a bug fix
        candidates.append(
            Candidate(
                sha=sha.strip(),
                subject=subject.strip(),
                author_date=date.strip(),
                score=score,
                reasons=reasons,
                source_files=[c.path for c in source],
                test_files=[c.path for c in tests],
                source_lines_changed=lines,
            )
        )
    return sorted(candidates, key=lambda c: (-c.score, c.author_date, c.sha))


def resolve_commit(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def fix_source_patch(repo: Path, sha: str) -> tuple[str, list[str]]:
    """The fix's patch restricted to non-test source files, and those files."""
    files = [c.path for c in numstat(repo, sha) if is_source_file(c.path)]
    if not files:
        raise GitError(f"commit {sha[:8]} changes no source files outside tests")
    patch = git(repo, "show", "--format=", "--binary", sha, "--", *files)
    return patch, files


@contextmanager
def snapshot_repo(repo: Path, ref: str) -> Iterator[Path]:
    """A throwaway directory holding the tree at ``ref`` with a fresh, history-free git repo."""
    root = Path(tempfile.mkdtemp(prefix="jev-reviewers-"))
    try:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", ref],
            cwd=repo,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        if archive.returncode != 0:
            raise GitError(f"git archive {ref[:8]} failed: {archive.stderr.decode()[:300]}")
        untar = subprocess.run(
            ["tar", "-x", "-C", str(root)],
            input=archive.stdout,
            capture_output=True,
            timeout=GIT_TIMEOUT,
            check=False,
        )
        if untar.returncode != 0:
            raise GitError(f"could not unpack the snapshot: {untar.stderr.decode()[:300]}")
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def commit_all(cwd: Path, message: str, *, branch: str | None = None) -> str:
    if branch:
        git(cwd, "checkout", "-q", "-B", branch)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", message, env=NEUTRAL_AUTHOR_ENV)
    return git(cwd, "rev-parse", "HEAD").strip()


def init_repo(cwd: Path) -> None:
    git(cwd, "init", "-q")
    git(cwd, "config", "commit.gpgsign", "false")
    git(cwd, "config", "user.name", BOT_IDENTITY[0])
    git(cwd, "config", "user.email", BOT_IDENTITY[1])


def apply_patch(cwd: Path, patch: str, *, reverse: bool) -> None:
    args = ["apply", "--whitespace=nowarn", "--index"]
    if reverse:
        args.append("-R")
    git(cwd, *args, "-", input_text=patch)


def _ranges(numbers: list[int]) -> list[Range]:
    merged: list[Range] = []
    for number in sorted(set(numbers)):
        if merged and number <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], number)
        else:
            merged.append((number, number))
    return merged


def target_hunks(diff_text: str) -> list[TargetHunk]:
    """Changed-line ranges for each file in a diff, on both sides, from ``unidiff``.

    Added lines are the right-side range. Removed lines are the left-side range; they also anchor
    the right-side position where the removal happened, since a bug that deletes a check is
    otherwise invisible on the right.
    """
    hunks: list[TargetHunk] = []
    for patched in PatchSet(diff_text):
        new_lines: list[int] = []
        old_lines: list[int] = []
        for hunk in patched:
            for line in hunk:
                if line.is_added and line.target_line_no is not None:
                    new_lines.append(line.target_line_no)
                elif line.is_removed and line.source_line_no is not None:
                    old_lines.append(line.source_line_no)
                    anchor = _anchor_after(hunk, line)
                    if anchor is not None:
                        new_lines.append(anchor)
        path = patched.path
        hunks.append(
            TargetHunk(path=path, new_ranges=_ranges(new_lines), old_ranges=_ranges(old_lines))
        )
    return hunks


def _anchor_after(hunk, removed_line) -> int | None:  # type: ignore[no-untyped-def]
    found = False
    for line in hunk:
        if found and line.target_line_no is not None:
            return int(line.target_line_no)
        if line is removed_line:
            found = True
    return None


def basic_auth_header(token: str) -> str:
    """The ``Authorization`` value git's http transport accepts for a GitHub token."""
    raw = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"Authorization: Basic {raw}"
