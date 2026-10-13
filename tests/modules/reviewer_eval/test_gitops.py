from __future__ import annotations

from pathlib import Path

import pytest
from tests.modules.reviewer_eval.conftest import BUGGY, FIXED, Repo, run

from jev_harness.modules.reviewer_eval import gitops


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_calc.py", True),
        ("src/calc_test.go", True),
        ("web/app.test.ts", True),
        ("web/app.spec.tsx", True),
        ("pkg/__tests__/a.js", True),
        ("conftest.py", True),
        ("src/calc.py", False),
        ("src/contest.py", False),
        ("docs/guide.md", False),
    ],
)
def test_is_test_file(path: str, expected: bool) -> None:
    assert gitops.is_test_file(path) is expected


def test_source_files_exclude_tests_and_non_code() -> None:
    assert gitops.is_source_file("src/calc.py")
    assert not gitops.is_source_file("tests/test_calc.py")
    assert not gitops.is_source_file("README.md")


def test_mining_ranks_a_linked_fix_first_and_skips_the_rest(source_repo: Repo) -> None:
    found = gitops.mine_candidates(source_repo.path, max_lines=40)
    assert [c.sha for c in found] == [source_repo.fix, source_repo.refactor]
    fix, refactor = found
    assert fix.score == 3 + 2 + 2 + 2
    assert fix.reasons == [
        "touches source and tests",
        "message mentions a fix",
        "links an issue",
        "small change (2 lines)",
    ]
    assert fix.source_files == ["src/calc.py"]
    assert fix.test_files == ["tests/test_calc.py"]
    assert fix.confirmed is False  # nothing is ever auto-accepted
    assert refactor.score == 3 + 2
    # Excluded: docs-only, the oversized change, and the revert.
    shas = {c.sha for c in found}
    assert source_repo.big not in shas
    assert source_repo.revert not in shas


def test_mining_respects_size_and_limit(source_repo: Repo) -> None:
    assert gitops.mine_candidates(source_repo.path, max_lines=1) == []
    assert gitops.mine_candidates(source_repo.path, limit=1) == []  # the newest commit is a revert


def test_patch_for_a_fix_only_touches_source_files(source_repo: Repo) -> None:
    patch, files = gitops.fix_source_patch(source_repo.path, source_repo.fix)
    assert files == ["src/calc.py"]
    assert "test_calc" not in patch
    with pytest.raises(gitops.GitError, match="no source files"):
        gitops.fix_source_patch(source_repo.path, run(source_repo.path, "rev-parse", "HEAD~4"))


def test_reverse_patch_reintroduces_the_bug_and_locates_it(source_repo: Repo) -> None:
    patch, _ = gitops.fix_source_patch(source_repo.path, source_repo.fix)
    with gitops.snapshot_repo(source_repo.path, source_repo.fix) as work:
        assert (work / "src" / "calc.py").read_text() == FIXED
        assert not (work / ".git").exists()
        gitops.init_repo(work)
        gitops.commit_all(work, "base", branch="base")
        gitops.apply_patch(work, patch, reverse=True)
        gitops.commit_all(work, "head", branch="head")
        assert (work / "src" / "calc.py").read_text() == BUGGY
        hunks = gitops.target_hunks(gitops.git(work, "diff", "base", "head"))
    assert [(h.path, h.new_ranges, h.old_ranges) for h in hunks] == [
        ("src/calc.py", [(4, 4)], [(4, 4)])
    ]


def test_removed_lines_anchor_a_pure_deletion() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,5 +1,4 @@\n"
        " line1\n line2\n-    guard()\n line4\n line5\n"
    )
    (hunk,) = gitops.target_hunks(diff)
    assert hunk.old_ranges == [(3, 3)]
    assert hunk.new_ranges == [(3, 3)]  # where the check used to be, on the right side


def test_remote_slugs_parse_https_and_ssh(source_repo: Repo) -> None:
    run(source_repo.path, "remote", "add", "origin", "https://github.com/Acme/App.git")
    run(source_repo.path, "remote", "add", "up", "git@github.com:acme/other.git")
    assert gitops.remote_slugs(source_repo.path) == {"acme/app", "acme/other"}


def test_git_errors_are_harness_errors(tmp_path: Path) -> None:
    with pytest.raises(gitops.GitError):
        gitops.require_repo(tmp_path)
    with pytest.raises(gitops.GitError, match="not a directory"):
        gitops.require_repo(tmp_path / "missing")
