from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from tests.modules.reviewer_eval.conftest import BUGGY, FIXED, Repo, run
from tests.modules.reviewer_eval.github_mock import REPO, FakeGitHub, inline

from jev_harness.core.errors import ConfigError
from jev_harness.modules.reviewer_eval import build, collect, gitops, service
from jev_harness.modules.reviewer_eval.github import GitHubClient, GitHubError, resolve_token
from jev_harness.modules.reviewer_eval.model import Manifest, PRRecord, load_manifest, save_manifest


def bare_show(bare: Path, ref: str, path: str) -> str:
    return subprocess.run(
        ["git", "--git-dir", str(bare), "show", f"{ref}:{path}"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip


def bare_branches(bare: Path) -> list[str]:
    out = subprocess.run(
        ["git", "--git-dir", str(bare), "for-each-ref", "--format=%(refname:short)", "refs/heads"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return sorted(out.split())


def source_state(repo: Path) -> tuple[str, str, str]:
    return (
        run(repo, "rev-parse", "HEAD"),
        run(repo, "for-each-ref", "--format=%(refname)"),
        run(repo, "status", "--porcelain"),
    )


async def go(plan: build.BuildPlan, confirm: bool = True) -> Manifest:
    with respx.mock(assert_all_called=False) as router:
        FakeGitHub().install(router)
        async with GitHubClient("tkn") as client:
            return await build.execute_build(plan, client, None, lambda lines: confirm)


def make_plan(repo: Repo, bare: Path, **kwargs: object) -> build.BuildPlan:
    return build.plan_build(repo.path, REPO, [repo.fix], git_url=str(bare), **kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------------- plan


def test_plan_names_every_ref_and_warns_about_third_party_reviewers(
    source_repo: Repo, bare_target: Path
) -> None:
    plan = make_plan(source_repo, bare_target)
    assert [(p.kind, p.id) for p in plan.prs] == [
        ("bug", f"bug-{source_repo.fix[:8]}"),
        ("control", f"control-{source_repo.fix[:8]}"),
    ]
    text = "\n".join(plan.confirmation_lines())
    assert REPO in text
    for ref in plan.refs():
        assert ref in text
    assert "reviewer app installed on the target repository will read this code" in text
    assert "never pushes to or changes the source repository" in text
    # The title and body are derived from file names and never from the fix message.
    assert plan.prs[0].title == "Update calc handling"
    assert "off-by-one" not in plan.prs[0].title + plan.prs[0].body


def test_plan_refuses_the_source_repository_as_target(source_repo: Repo) -> None:
    run(source_repo.path, "remote", "add", "origin", "git@github.com:Acme/Sandbox.git")
    with pytest.raises(ConfigError, match="is a remote of the source repository"):
        build.plan_build(source_repo.path, "acme/sandbox", [source_repo.fix])


@pytest.mark.parametrize("slug", ["no-slash", "a/b/c", "bad slug/x", ""])
def test_plan_validates_the_target_slug(source_repo: Repo, slug: str) -> None:
    with pytest.raises(ConfigError, match="owner/name"):
        build.plan_build(source_repo.path, slug, [source_repo.fix])


def test_plan_needs_fixes_and_valid_commits(source_repo: Repo) -> None:
    with pytest.raises(ConfigError, match="no fix commits"):
        build.plan_build(source_repo.path, REPO, [])
    with pytest.raises(gitops.GitError):
        build.plan_build(source_repo.path, REPO, ["deadbeef"])


def test_controls_can_be_disabled_and_root_commits_have_none(source_repo: Repo) -> None:
    plan = build.plan_build(source_repo.path, REPO, [source_repo.fix], controls=0)
    assert [p.kind for p in plan.prs] == ["bug"]


# ---------------------------------------------------------------------- build


async def test_build_pushes_a_bug_pr_and_a_control_pr_to_the_sandbox_only(
    source_repo: Repo, bare_target: Path
) -> None:
    before = source_state(source_repo.path)
    plan = make_plan(source_repo, bare_target)
    manifest = await go(plan)

    assert source_state(source_repo.path) == before  # the source repository was not touched
    assert bare_branches(bare_target) == sorted(plan.refs())

    bug, control = manifest.prs
    # Bug PR: base is the fixed code, head re-introduces the bug, tests keep the fix's test.
    assert bare_show(bare_target, bug.base_branch, "src/calc.py") == FIXED
    assert bare_show(bare_target, bug.head_branch, "src/calc.py") == BUGGY
    assert "test_high" in bare_show(bare_target, bug.head_branch, "tests/test_calc.py")
    assert [(t.path, t.new_ranges, t.old_ranges) for t in bug.targets] == [
        ("src/calc.py", [(4, 4)], [(4, 4)])
    ]
    assert bug.languages == ["py"]
    assert bug.directories == ["src"]
    # Control PR: the real fix applied forward, with nothing to find.
    assert bare_show(bare_target, control.base_branch, "src/calc.py") == BUGGY
    assert bare_show(bare_target, control.head_branch, "src/calc.py") == FIXED
    assert control.targets == []

    assert [(p.number, p.kind) for p in manifest.prs] == [(1, "bug"), (2, "control")]
    assert manifest.prs[0].created_at == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    # No history is pushed: each branch pair is a two-commit snapshot.
    count = subprocess.run(
        ["git", "--git-dir", str(bare_target), "rev-list", "--count", bug.head_branch],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert count == "2"
    commit_author = subprocess.run(
        ["git", "--git-dir", str(bare_target), "log", "-1", "--format=%an", bug.head_branch],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert commit_author == "jev-harness"


async def test_declining_the_confirmation_pushes_and_opens_nothing(
    source_repo: Repo, bare_target: Path
) -> None:
    plan = make_plan(source_repo, bare_target)
    with respx.mock(assert_all_called=False) as router:
        github = FakeGitHub()
        github.install(router)
        async with GitHubClient("tkn") as client:
            with pytest.raises(build.ConfirmationDeclinedError, match="nothing was pushed"):
                await build.execute_build(plan, client, None, lambda lines: False)
    assert bare_branches(bare_target) == []
    assert github.requests == []  # not even a read


async def test_excluded_paths_are_removed_from_every_snapshot(
    source_repo: Repo, bare_target: Path
) -> None:
    plan = make_plan(source_repo, bare_target, excludes=["README.md"])
    manifest = await go(plan)
    files = subprocess.run(
        ["git", "--git-dir", str(bare_target), "ls-tree", "-r", "--name-only", manifest.prs[0].head_branch],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert "README.md" not in files
    assert "src/calc.py" in files


def test_a_push_failure_never_leaks_the_token(source_repo: Repo, tmp_path: Path) -> None:
    secret = "ghp_SECRETTOKEN123"
    with gitops.snapshot_repo(source_repo.path, source_repo.fix) as work:
        gitops.init_repo(work)
        gitops.commit_all(work, "x", branch="b")
        with pytest.raises(gitops.GitError) as excinfo:
            build._push(work, f"file:///nonexistent/{secret}.git", ["b"], secret)
    assert secret not in str(excinfo.value)


@pytest.mark.parametrize(("answer", "expected"), [("yes", True), ("y", False), ("", False)])
def test_input_confirm_requires_the_word_yes(answer: str, expected: bool) -> None:
    assert build.input_confirm(["line"], assume_yes=False, read=lambda prompt: answer) is expected
    assert build.input_confirm(["line"], assume_yes=True) is True


# ----------------------------------------------------------------- collection


def manifest_with_prs() -> Manifest:
    created = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    return Manifest(
        target_repo=REPO,
        source_repo="x",
        prs=[
            PRRecord(id="bug-a", kind="bug", fix_sha="a" * 40, base_branch="b1", head_branch="h1",
                     title="t", number=1, created_at=created),
            PRRecord(id="control-a", kind="control", fix_sha="a" * 40, base_branch="b2",
                     head_branch="h2", title="t", number=2, created_at=created),
        ],
    )  # fmt: skip


async def test_comments_from_all_three_endpoints_are_normalised_and_the_owner_is_excluded() -> None:
    github = FakeGitHub()
    github.review_comments[1] = [
        inline("bot-a", "src/calc.py", 4, start_line=3),
        inline("owner-user", "src/calc.py", 4, "my own comment"),
        inline("bot-a", "src/calc.py", 4, side="LEFT"),
    ]
    github.reviews[1] = [
        {"user": {"login": "bot-a"}, "body": "Overall looks fine", "submitted_at": "2026-10-01T10:06:00Z"},
        {"user": {"login": "bot-b"}, "body": "", "submitted_at": "2026-10-01T10:06:00Z"},
    ]  # fmt: skip
    github.issue_comments[2] = [
        {"user": {"login": "bot-b"}, "body": "Summary", "created_at": "2026-10-01T10:07:00Z"}
    ]
    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(manifest_with_prs(), client, reviewers=[])
    kinds = sorted((c.reviewer, c.pr_id, c.kind, c.side) for c in updated.comments)
    assert kinds == [
        ("bot-a", "bug-a", "inline", "LEFT"),
        ("bot-a", "bug-a", "inline", "RIGHT"),
        ("bot-a", "bug-a", "summary", "RIGHT"),
        ("bot-b", "control-a", "summary", "RIGHT"),
    ]
    start = next(c for c in updated.comments if c.start_line)
    assert (start.start_line, start.line) == (3, 4)


async def test_collect_polls_until_every_expected_reviewer_answered() -> None:
    github = FakeGitHub()
    ticks = {"now": 0.0, "sleeps": 0}

    async def fake_sleep(seconds: float) -> None:
        ticks["now"] += seconds
        ticks["sleeps"] += 1
        if ticks["sleeps"] == 2:  # the reviewer shows up on both PRs after two polls
            github.review_comments[1] = [inline("bot-a", "src/calc.py", 4)]
            github.review_comments[2] = [inline("bot-a", "src/calc.py", 9)]

    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(
                manifest_with_prs(), client, reviewers=["bot-a"], wait_seconds=600,
                interval_seconds=30, sleep=fake_sleep, clock=lambda: ticks["now"],
            )  # fmt: skip
    assert ticks["sleeps"] == 2
    assert len(updated.comments) == 2
    assert "every expected reviewer answered every pull request" in updated.collection_notes
    assert updated.expected_reviewers == ["bot-a"]


async def test_collect_gives_up_at_the_deadline_and_says_so() -> None:
    github = FakeGitHub()
    ticks = {"now": 0.0}

    async def fake_sleep(seconds: float) -> None:
        ticks["now"] += seconds

    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(
                manifest_with_prs(), client, reviewers=["bot-a"], wait_seconds=90,
                interval_seconds=30, sleep=fake_sleep, clock=lambda: ticks["now"],
            )  # fmt: skip
    assert updated.comments == []
    assert "stopped waiting after 90s" in updated.collection_notes
    assert updated.expected_reviewers == ["bot-a"]  # its absence stays visible in the report


async def test_collect_stops_once_comments_stop_arriving() -> None:
    github = FakeGitHub()
    github.review_comments[1] = [inline("bot-a", "src/calc.py", 4)]
    ticks = {"now": 0.0}

    async def fake_sleep(seconds: float) -> None:
        ticks["now"] += seconds

    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(
                manifest_with_prs(), client, reviewers=["bot-a", "bot-b"], wait_seconds=3600,
                interval_seconds=30, settle_seconds=60, sleep=fake_sleep, clock=lambda: ticks["now"],
            )  # fmt: skip
    assert any("no new comments for 60s" in n for n in updated.collection_notes)


async def test_pagination_follows_the_link_header() -> None:
    page_one = [inline("bot-a", "src/calc.py", n) for n in (1, 2)]
    page_two = [inline("bot-a", "src/calc.py", 3)]
    with respx.mock(base_url="https://api.github.com") as router:
        router.get(f"/repos/{REPO}/pulls/1/comments").mock(
            side_effect=[
                __import__("httpx").Response(
                    200, json=page_one,
                    headers={"link": f'<https://api.github.com/repos/{REPO}/pulls/1/comments?page=2>; rel="next"'},
                ),
                __import__("httpx").Response(200, json=page_two),
            ]
        )  # fmt: skip
        async with GitHubClient("tkn") as client:
            items = await client.review_comments(REPO, 1)
    assert [i["line"] for i in items] == [1, 2, 3]


# --------------------------------------------------------------- local review


def test_local_reviewer_specs_and_output_formats() -> None:
    name, argv = collect.parse_local_spec("claude=claude -p {diff}")
    assert (name, argv) == ("local:claude", ["claude", "-p", "{diff}"])
    for bad in ("nocommand", "=x {diff}", "x=no placeholder"):
        with pytest.raises(ConfigError):
            collect.parse_local_spec(bad)

    pr = manifest_with_prs().prs[0]
    when = datetime(2026, 10, 1, 10, 3, tzinfo=UTC)
    from_json = collect.parse_local_output(
        "local:x", pr,
        '[{"path": "src/calc.py", "line": 4, "body": "off by one"}, {"body": "overall fine"}, {"path": "a"}]',
        when,
    )  # fmt: skip
    assert [(c.kind, c.path, c.line) for c in from_json] == [
        ("inline", "src/calc.py", 4),
        ("summary", None, None),
    ]
    lines = collect.parse_local_output(
        "local:x", pr, "src/calc.py:4: looks wrong\nsome prose", when
    )
    assert [(c.kind, c.line) for c in lines] == [("inline", 4)]
    prose = collect.parse_local_output("local:x", pr, "just words", when)
    assert [c.kind for c in prose] == ["summary"]
    assert collect.parse_local_output("local:x", pr, "  ", when) == []


async def test_local_reviewers_run_on_the_pr_diff_and_failures_are_noted() -> None:
    seen: list[list[str]] = []

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(command)
        diff = Path(command[-1]).read_text()
        assert diff.startswith("diff --git")
        if "bad" in command[0]:
            return subprocess.CompletedProcess(command, 3, "", "boom")
        return subprocess.CompletedProcess(command, 0, "src/calc.py:4: off by one\n", "")

    github = FakeGitHub()
    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.run_local_reviewers(
                manifest_with_prs(), client, ["good=/bin/good {diff}", "bad=/bin/bad {diff}"], runner=runner
            )  # fmt: skip
    assert len(seen) == 4
    assert {c.reviewer for c in updated.comments} == {"local:good"}
    assert sum("exited 3" in n for n in updated.collection_notes) == 2
    assert set(updated.expected_reviewers) == {"local:good", "local:bad"}


# -------------------------------------------------------------------- cleanup


async def test_cleanup_closes_prs_deletes_branches_and_is_idempotent(tmp_path: Path) -> None:
    manifest = manifest_with_prs()
    github = FakeGitHub()
    github.pulls = {1: {"state": "open"}, 2: {"state": "open"}}
    github.missing_branches = {"h2"}
    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated, actions = await service.cleanup(manifest, client)
            again, more = await service.cleanup(updated, client)
    assert [p["state"] for p in github.pulls.values()] == ["closed", "closed"]
    assert github.deleted == ["h1", "b1", "b2"]
    assert "already gone: h2" in actions
    assert all(p.closed for p in updated.prs)
    assert more == []  # a second run has nothing left to do
    assert again == updated
    save_manifest(updated, tmp_path / "m.json")
    assert load_manifest(tmp_path / "m.json") == updated


# --------------------------------------------------------------------- client


async def test_api_errors_are_actionable_and_never_contain_the_token() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.github.com").mock(
            return_value=__import__("httpx").Response(404, json={"message": "Not Found tkn-secret"})
        )
        async with GitHubClient("tkn-secret") as client:
            with pytest.raises(GitHubError) as excinfo:
                await client.get_repo(REPO)
    assert "404" in str(excinfo.value)
    assert "tkn-secret" not in str(excinfo.value)


async def test_unauthorised_and_rate_limited_responses() -> None:
    import httpx

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.github.com").mock(
            side_effect=[
                httpx.Response(401, json={}),
                httpx.Response(403, json={"message": "limit"}, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}),
            ]
        )  # fmt: skip
        async with GitHubClient("t") as client:
            with pytest.raises(GitHubError, match="401"):
                await client.get_repo(REPO)
            with pytest.raises(GitHubError, match="rate limit"):
                await client.get_repo(REPO)


async def test_transient_gateway_errors_are_retried() -> None:
    import httpx

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.github.com").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"login": "me"})]
        )
        async with GitHubClient("t", retry_delay=0) as client:
            assert await client.authenticated_login() == "me"


def test_token_resolution_prefers_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_token("configured") == "configured"
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(ConfigError, match="no GitHub token"):
        resolve_token(None)


async def test_trigger_comments_and_review_requests_are_sent_to_every_pr(
    source_repo: Repo, bare_target: Path
) -> None:
    plan = make_plan(
        source_repo,
        bare_target,
        trigger_comments=["@coderabbitai review"],
        request_reviewers=["some-bot"],
    )
    assert "posts this comment on every PR: '@coderabbitai review'" in "\n".join(
        plan.confirmation_lines()
    )
    github = FakeGitHub()
    with respx.mock(assert_all_called=False) as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            manifest = await build.execute_build(plan, client, None, lambda lines: True)
    assert github.posted == [(1, "@coderabbitai review"), (2, "@coderabbitai review")]
    assert github.review_requests == [(1, ["some-bot"]), (2, ["some-bot"])]
    assert manifest.collection_notes == []


async def test_a_rejected_review_request_is_recorded_and_the_prs_are_kept(
    source_repo: Repo, bare_target: Path
) -> None:
    plan = make_plan(source_repo, bare_target, request_reviewers=["not-a-collaborator"])
    github = FakeGitHub(reject_review_requests=True)
    with respx.mock(assert_all_called=False) as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            manifest = await build.execute_build(plan, client, None, lambda lines: True)
    assert [p.number for p in manifest.prs] == [1, 2]
    assert len(manifest.collection_notes) == 2
    assert "review request failed" in manifest.collection_notes[0]


# Bodies trimmed from what CodeRabbit really posted on the sandbox (2026-10-15).
PROCESSING = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n"
    "> [!NOTE]\n> Currently processing new changes in this PR. This may take a few minutes, please wait...\n"
)
RATE_LIMITED = (
    "<!-- This is an auto-generated comment: summarize by coderabbit.ai -->\n> [!WARNING]\n"
    "> ## Review limit reached\n> \n> You've used all free OSS reviews for now.\n"
)


async def test_status_notices_are_not_reviews_and_are_reported() -> None:
    github = FakeGitHub()
    bot = "coderabbitai[bot]"
    github.issue_comments[1] = [
        {"user": {"login": bot}, "body": PROCESSING, "created_at": "2026-10-01T10:01:00Z"}
    ]
    github.issue_comments[2] = [
        {"user": {"login": bot}, "body": RATE_LIMITED, "created_at": "2026-10-01T10:01:00Z"}
    ]
    ticks = {"now": 0.0}

    async def fake_sleep(seconds: float) -> None:
        ticks["now"] += seconds

    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(
                manifest_with_prs(), client, reviewers=[bot], wait_seconds=120,
                interval_seconds=30, settle_seconds=1000, sleep=fake_sleep, clock=lambda: ticks["now"],
            )  # fmt: skip
    # It kept waiting (a notice is not an answer) until the deadline, and says why.
    assert "every expected reviewer answered every pull request" not in updated.collection_notes
    assert "stopped waiting after 120s" in updated.collection_notes
    assert all(c.status for c in updated.comments)
    notes = "\n".join(updated.collection_notes)
    assert "Currently processing new changes" in notes
    assert "Review limit reached" in notes

    scores = service.build_report(updated, service.ReportOptions(min_prs=1))
    assert scores.metrics[f"coverage.{bot}"].startswith("0/2")  # notices are not coverage
    assert scores.metrics[f"summary_comments.{bot}"] == 0


async def test_a_real_review_after_a_notice_completes_the_wait() -> None:
    github = FakeGitHub()
    bot = "coderabbitai[bot]"
    github.issue_comments[1] = [
        {"user": {"login": bot}, "body": PROCESSING, "created_at": "2026-10-01T10:01:00Z"}
    ]
    ticks = {"now": 0.0, "n": 0}

    async def fake_sleep(seconds: float) -> None:
        ticks["now"] += seconds
        ticks["n"] += 1
        if ticks["n"] == 2:
            github.reviews[1] = [
                {
                    "user": {"login": bot},
                    "body": "Actionable comments posted: 1",
                    "submitted_at": "2026-10-01T10:05:00Z",
                }
            ]
            github.review_comments[1] = [
                inline(bot, "src/calc.py", 4, "Off-by-one: should be > high")
            ]
            github.reviews[2] = [
                {
                    "user": {"login": bot},
                    "body": "No actionable comments.",
                    "submitted_at": "2026-10-01T10:05:00Z",
                }
            ]

    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            updated = await collect.collect_github(
                manifest_with_prs(), client, reviewers=[bot], wait_seconds=600,
                interval_seconds=30, sleep=fake_sleep, clock=lambda: ticks["now"],
            )  # fmt: skip
    assert "every expected reviewer answered every pull request" in updated.collection_notes
    assert {c.origin for c in updated.comments if not c.status} == {"inline", "review"}


def test_status_detection_on_real_and_ordinary_bodies() -> None:
    assert collect.is_status_notice(PROCESSING)
    assert collect.is_status_notice(RATE_LIMITED)
    assert collect.is_status_notice("Review rate limited. Your next review is in 59 minutes.")
    assert not collect.is_status_notice("Off-by-one: the upper bound should be `> high`.")
    assert not collect.is_status_notice(
        "## Walkthrough\nThe clamp function changes its upper bound check."
    )
    assert "Review limit reached" in collect.status_line(RATE_LIMITED)


async def test_cleanup_never_claims_to_delete_the_default_branch() -> None:
    """Seen live: pushing to an empty sandbox made a base branch the repository default."""
    manifest = manifest_with_prs()
    github = FakeGitHub(default_branch="b1")
    github.pulls = {1: {"state": "open"}, 2: {"state": "open"}}
    with respx.mock() as router:
        github.install(router)
        async with GitHubClient("tkn") as client:
            _, actions = await service.cleanup(manifest, client)
    assert "b1" not in github.deleted
    assert any(a.startswith("kept: b1 is the repository's default branch") for a in actions)
    assert not any(a == "already gone: b1" for a in actions)


async def test_an_unexpected_refusal_is_reported_not_called_gone() -> None:
    import httpx

    with respx.mock(assert_all_called=False) as router:
        router.delete(f"https://api.github.com/repos/{REPO}/git/refs/heads/x").mock(
            return_value=httpx.Response(422, json={"message": "Branch is protected"})
        )
        async with GitHubClient("tkn") as client:
            with pytest.raises(GitHubError, match="protected"):
                await client.delete_branch(REPO, "x")
