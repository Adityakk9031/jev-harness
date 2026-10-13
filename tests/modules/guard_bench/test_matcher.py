from __future__ import annotations

from pathlib import Path

import pytest

from jev_harness.modules.guard_bench.corpus import Payload
from jev_harness.modules.guard_bench.decision import Outcome
from jev_harness.modules.guard_bench.discovery import PermissionRule
from jev_harness.modules.guard_bench.matcher import PermissionMatcher


def bash(command: str) -> Payload:
    return Payload(
        id="x", base_id="x", category="c", intent="i", disguise="base",
        expected="block", tool="Bash", tool_input={"command": command},
    )  # fmt: skip


def files(tool: str, path: str) -> Payload:
    return Payload(
        id="x", base_id="x", category="c", intent="i", disguise="base",
        expected="block", tool=tool, tool_input={"file_path": path},
    )  # fmt: skip


def matcher(*specs: tuple[str, str], tmp: Path) -> PermissionMatcher:
    rules = [PermissionRule(kind=k, spec=s, source="t") for k, s in specs]  # type: ignore[arg-type]
    return PermissionMatcher(rules, tmp / "proj", tmp / "home")


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        ("rm -rf x", Outcome.block),
        ("true && rm -rf x", Outcome.block),
        ("echo ok | rm x", Outcome.block),
        ("(rm -rf x)", Outcome.block),
        ("echo start\nrm x", Outcome.block),
        ("r''m -rf x", Outcome.block),  # bashlex removes the quoting
        ("echo $(rm -rf x)", Outcome.no_opinion),  # not looked inside: a documented gap
        ("bash -c 'rm -rf x'", Outcome.no_opinion),
        ("X=rm; $X -rf x", Outcome.no_opinion),
        ("git status", Outcome.no_opinion),
        ("grep rm notes.txt", Outcome.no_opinion),  # "rm" as an argument is not the rm command
    ],
)
def test_bash_deny_rule_prefix(tmp_path: Path, command: str, outcome: Outcome) -> None:
    assert matcher(("deny", "Bash(rm:*)"), tmp=tmp_path).decide(bash(command)).outcome is outcome


def test_wildcard_and_exact_bash_rules(tmp_path: Path) -> None:
    m = matcher(("deny", "Bash(git push *)"), ("deny", "Bash(printenv)"), tmp=tmp_path)
    assert m.decide(bash("git push --force origin main")).outcome is Outcome.block
    assert m.decide(bash("git pushx")).outcome is Outcome.no_opinion
    assert m.decide(bash("printenv")).outcome is Outcome.block
    assert m.decide(bash("printenv HOME")).outcome is Outcome.no_opinion


def test_deny_beats_ask_beats_allow(tmp_path: Path) -> None:
    m = matcher(
        ("allow", "Bash(rm:*)"), ("ask", "Bash(rm:*)"), ("deny", "Bash(rm -rf:*)"), tmp=tmp_path
    )
    assert m.decide(bash("rm -rf x")).outcome is Outcome.block
    assert m.decide(bash("rm x")).outcome is Outcome.ask
    only_allow = matcher(("allow", "Bash(ls:*)"), tmp=tmp_path)
    assert only_allow.decide(bash("ls -la")).outcome is Outcome.allow
    assert only_allow.decide(bash("ls && rm x")).outcome is Outcome.no_opinion


def test_bare_tool_rule_matches_everything_for_that_tool(tmp_path: Path) -> None:
    m = matcher(("deny", "Bash"), tmp=tmp_path)
    assert m.decide(bash("anything")).outcome is Outcome.block
    assert m.decide(files("Read", "a.txt")).outcome is Outcome.no_opinion


@pytest.mark.parametrize(
    ("path", "outcome"),
    [
        (".env", Outcome.block),
        ("./.env", Outcome.block),
        ("src/../.env", Outcome.block),
        ("{proj}/.env", Outcome.block),
        ("src/app.py", Outcome.no_opinion),
        (".env.example", Outcome.no_opinion),
    ],
)
def test_read_deny_rule_normalises_paths(tmp_path: Path, path: str, outcome: Outcome) -> None:
    m = matcher(("deny", "Read(./.env)"), tmp=tmp_path)
    resolved = path.replace("{proj}", str((tmp_path / "proj").resolve()))
    assert m.decide(files("Read", resolved)).outcome is outcome


def test_home_and_recursive_globs(tmp_path: Path) -> None:
    m = matcher(("deny", "Read(~/.ssh/**)"), ("deny", "Read(**/secrets/*.key)"), tmp=tmp_path)
    assert m.decide(files("Read", "~/.ssh/id_rsa")).outcome is Outcome.block
    assert m.decide(files("Read", "~/.ssh/x/../id_rsa")).outcome is Outcome.block
    assert m.decide(files("Read", "app/secrets/a.key")).outcome is Outcome.block
    assert m.decide(files("Read", "app/secrets/a.txt")).outcome is Outcome.no_opinion


def test_edit_rule_covers_write_and_not_read(tmp_path: Path) -> None:
    m = matcher(("deny", "Edit(.claude/**)"), tmp=tmp_path)
    assert m.decide(files("Write", ".claude/settings.json")).outcome is Outcome.block
    assert m.decide(files("Edit", ".claude/settings.json")).outcome is Outcome.block
    assert m.decide(files("Read", ".claude/settings.json")).outcome is Outcome.no_opinion


def test_read_rule_does_not_cover_shell_cat(tmp_path: Path) -> None:
    m = matcher(("deny", "Read(./.env)"), tmp=tmp_path)
    assert m.decide(bash("cat .env")).outcome is Outcome.no_opinion
