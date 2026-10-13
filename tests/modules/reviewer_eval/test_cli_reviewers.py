from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from tests.modules.reviewer_eval.conftest import Repo
from tests.modules.reviewer_eval.github_mock import REPO, FakeGitHub, inline
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.report import Report

runner = CliRunner()


def invoke(*args: str, input_text: str | None = None):
    return runner.invoke(app, ["reviewers", *args], input=input_text)


@pytest.fixture(autouse=True)
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


def test_mine_writes_candidates_for_a_person_to_confirm(source_repo: Repo, tmp_path: Path) -> None:
    out = tmp_path / "c.json"
    result = invoke("mine", "--repo", str(source_repo.path), "-o", str(out))
    assert result.exit_code == 0, result.output
    assert source_repo.fix[:8] in result.output
    assert "Fix off-by-one in clamp (#12)" in result.output
    written = json.loads(out.read_text())
    assert [c["sha"] for c in written] == [source_repo.fix, source_repo.refactor]
    assert {c["confirmed"] for c in written} == {False}


def test_mine_with_nothing_found_says_why(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path / "r")], check=True)
    result = invoke("mine", "--repo", str(tmp_path / "r"), "-o", str(tmp_path / "c.json"))
    assert result.exit_code in (0, 2)


def test_build_dry_run_plans_but_builds_and_pushes_nothing(
    source_repo: Repo, bare_target: Path
) -> None:
    result = invoke(
        "build", "--repo", str(source_repo.path), "--target-repo", REPO,
        "--fix", source_repo.fix, "--git-url", str(bare_target), "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "branches to push (4)" in result.output
    assert "will read this code" in result.output
    assert "dry run: nothing was built, pushed, or opened." in result.output


def test_build_without_confirmation_exits_two_and_pushes_nothing(
    source_repo: Repo, bare_target: Path
) -> None:
    result = invoke(
        "build", "--repo", str(source_repo.path), "--target-repo", REPO,
        "--fix", source_repo.fix, "--git-url", str(bare_target),
    )  # fmt: skip
    assert result.exit_code == 2
    assert "not confirmed; nothing was pushed" in result.output
    assert not list(bare_target.glob("refs/heads/*/*"))


def test_build_refuses_unconfirmed_candidate_files(source_repo: Repo, tmp_path: Path) -> None:
    candidates = tmp_path / "c.json"
    candidates.write_text(json.dumps([{"sha": source_repo.fix, "confirmed": False}]))
    result = invoke(
        "build", "--repo", str(source_repo.path), "--target-repo", REPO,
        "--candidates", str(candidates), "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 2
    assert '"confirmed": true' in result.output


def test_build_refuses_the_source_repository(source_repo: Repo) -> None:
    import subprocess

    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{REPO}.git"],
        cwd=source_repo.path, check=True,
    )  # fmt: skip
    result = invoke(
        "build", "--repo", str(source_repo.path), "--target-repo", REPO, "--fix", source_repo.fix,
        "--dry-run",
    )  # fmt: skip
    assert result.exit_code == 2
    assert "is a remote of the source repository" in result.output


def test_the_whole_flow_end_to_end(source_repo: Repo, bare_target: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    candidates = tmp_path / "candidates.json"
    assert invoke("mine", "--repo", str(source_repo.path), "-o", str(candidates)).exit_code == 0
    entries = json.loads(candidates.read_text())
    entries[0]["confirmed"] = True  # the person confirms the first candidate only
    candidates.write_text(json.dumps(entries))

    github = FakeGitHub()
    github.pulls = {}
    with respx.mock(assert_all_called=False) as router:
        github.install(router)
        built = invoke(
            "build", "--repo", str(source_repo.path), "--target-repo", REPO,
            "--candidates", str(candidates), "--git-url", str(bare_target),
            "--manifest", str(manifest), "--yes",
        )  # fmt: skip
        assert built.exit_code == 0, built.output
        assert "bug      bug-" in built.output
        assert "control  control-" in built.output

        # Reviewers answer: one precise on the bug PR, one that only chats on the control PR.
        github.review_comments[1] = [inline("precise-bot", "src/calc.py", 4, created_at="x")]
        github.review_comments[1][0]["created_at"] = "2026-10-01T10:00:45Z"
        github.review_comments[2] = [inline("chatty-bot", "src/calc.py", 1)]
        collected = invoke("collect", "--manifest", str(manifest), "--reviewer", "precise-bot",
                           "--reviewer", "chatty-bot")  # fmt: skip
        assert collected.exit_code == 0, collected.output
        assert "collected 2 comment(s) from 2 reviewer(s)" in collected.output
        assert "reviewers cleanup" in collected.output

        report = invoke(
            "report", "--manifest", str(manifest), "--format", "json", "--min-prs", "1",
            "--noise-ceiling", "0.4",
        )  # fmt: skip
        assert report.exit_code == 1  # chatty-bot talked on a control PR: 0.5 comments per PR
        parsed = Report.model_validate(json.loads(report.output))
        assert parsed.metrics["recall.precise-bot"].startswith("1/1 bugs")
        assert parsed.metrics["recall.chatty-bot"].startswith("0/1 bugs")
        assert parsed.metrics["median_time_to_first_comment_s.precise-bot"] == 45.0
        assert parsed.meta["target_repo"] == REPO

        dry = invoke("cleanup", "--manifest", str(manifest), "--dry-run")
        assert dry.exit_code == 0
        assert github.deleted == []
        declined = invoke("cleanup", "--manifest", str(manifest), input_text="no\n")
        assert declined.exit_code == 2
        assert github.deleted == []
        kept = invoke("cleanup", "--manifest", str(manifest), "--keep")
        assert kept.exit_code == 0
        assert github.deleted == []

        cleaned = invoke("cleanup", "--manifest", str(manifest), "--yes")
        assert cleaned.exit_code == 0, cleaned.output
        assert len(github.deleted) == 4
        assert {p["state"] for p in github.pulls.values()} == {"closed"}
        again = invoke("cleanup", "--manifest", str(manifest), "--yes")
        assert "nothing left to clean up." in again.output


def test_collect_and_report_dry_runs_and_missing_manifest(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    for command in ("collect", "report", "cleanup"):
        result = invoke(command, "--manifest", str(missing))
        assert result.exit_code == 2
        assert "manifest not found" in result.output


def test_local_reviewer_spec_is_validated_before_any_request(tmp_path: Path) -> None:
    from jev_harness.modules.reviewer_eval import Manifest, save_manifest

    path = tmp_path / "m.json"
    save_manifest(Manifest(target_repo=REPO, source_repo="x"), path)
    with respx.mock(assert_all_called=False) as router:
        FakeGitHub().install(router)
        result = invoke(
            "collect", "--manifest", str(path), "--local-reviewer", "no-placeholder=echo hi"
        )
    assert result.exit_code == 2
    assert "{diff}" in result.output


@pytest.mark.parametrize("command", ["mine", "build", "collect", "report"])
def test_judge_is_none_only(command: str, tmp_path: Path) -> None:
    args = ["--target-repo", REPO] if command == "build" else []
    result = invoke(command, *args, "--judge", "anthropic")
    assert result.exit_code == 2
    assert "--judge must be 'none'" in result.output
