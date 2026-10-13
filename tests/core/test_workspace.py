"""Workspace tests, including the guards that keep us off the user's real tree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jev_harness.core.workspace import (
    Workspace,
    WorkspaceError,
    WorkspaceMode,
    temp_root,
)


def make_repo(root: Path) -> Path:
    """A tiny git repository with one commit."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text("root instructions\n")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    for argv in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "initial"]):
        subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True)
    return root


def test_empty_workspace_plants_files_and_cleans_up() -> None:
    with Workspace.create() as workspace:
        assert workspace.path.is_dir()
        assert workspace.home.is_dir()
        assert workspace.mode is WorkspaceMode.empty
        planted = workspace.plant_file("nested/CLAUDE.md", "canary\n")
        assert planted.read_text() == "canary\n"
        assert workspace.read_file("nested/CLAUDE.md") == "canary\n"
        assert workspace.exists("nested/CLAUDE.md")
        assert workspace.planted == ["nested/CLAUDE.md"]
        assert workspace.list_files() == ["nested/CLAUDE.md"]
        path = workspace.path
        home = workspace.home
    assert not path.exists()
    assert not home.exists()
    assert workspace.closed


def test_close_is_idempotent() -> None:
    workspace = Workspace.create()
    workspace.close()
    workspace.close()
    assert workspace.closed


def test_copy_mode_copies_the_tree_and_skips_git() -> None:
    source = make_repo(temp_root() / "fixture-repo" / "copy-src")
    with Workspace.create(source) as workspace:
        assert workspace.mode is WorkspaceMode.copy
        assert workspace.read_file("CLAUDE.md") == "root instructions\n"
        assert workspace.list_files() == ["CLAUDE.md", "src/app.py"]
        assert not (workspace.path / ".git").exists()


def test_copy_mode_can_include_git() -> None:
    source = make_repo(temp_root() / "fixture-repo" / "git-src")
    with Workspace.create(source, include_git=True) as workspace:
        assert (workspace.path / ".git").exists()
        assert ".git" in workspace.list_files(include_dot_dirs=True)[0]


def test_differs_from_source_reports_edits_additions_and_removals() -> None:
    source = make_repo(temp_root() / "fixture-repo" / "diff-src")
    with Workspace.create(source) as workspace:
        assert workspace.differs_from_source() == []

        # What the agent would do: edit a file, add one, delete one.
        (workspace.path / "src" / "app.py").write_text("print('changed')\n")
        (workspace.path / "added.txt").write_text("new\n")
        (workspace.path / "CLAUDE.md").unlink()
        assert workspace.differs_from_source() == ["CLAUDE.md", "added.txt", "src/app.py"]

        # Files we planted ourselves are not the agent's changes.
        workspace.plant_file("planted-only.txt", "planted\n")
        assert workspace.planted == ["planted-only.txt"]
        assert "planted-only.txt" not in workspace.differs_from_source()


def test_planting_cannot_escape_the_workspace() -> None:
    with Workspace.create() as workspace:
        with pytest.raises(WorkspaceError, match="must be relative"):
            workspace.plant_file("/etc/passwd", "x")
        with pytest.raises(WorkspaceError, match="escapes the workspace"):
            workspace.plant_file("../escape.txt", "x")
        with pytest.raises(WorkspaceError, match="escapes the workspace"):
            workspace.resolve("../../etc/passwd")


def test_a_manually_built_workspace_outside_the_temp_root_refuses_to_write(
    tmp_path: Path,
) -> None:
    """The strongest guard: writes only ever happen inside our own temp root."""
    hand_built = Workspace(path=tmp_path, home=tmp_path / "home", mode=WorkspaceMode.empty)
    with pytest.raises(WorkspaceError, match="only writes inside its own disposable workspace"):
        hand_built.plant_file("CLAUDE.md", "should not be written")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_refuses_a_source_that_contains_the_working_directory() -> None:
    with pytest.raises(WorkspaceError, match="contains the current working directory"):
        Workspace.create(Path.cwd())


def test_source_must_exist_and_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="needs a source directory"):
        Workspace.create(mode="copy")
    with pytest.raises(WorkspaceError, match="not a directory"):
        Workspace.create(tmp_path / "absent")


def test_no_source_defaults_to_an_empty_workspace() -> None:
    with Workspace.create() as workspace:
        assert workspace.mode is WorkspaceMode.empty
        assert workspace.list_files() == []


def test_empty_mode_rejects_a_source(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="takes no source"):
        Workspace.create(tmp_path, mode="empty")


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid WorkspaceMode"):
        Workspace.create(mode="teleport")


def test_worktree_mode_uses_git_and_cleans_up_after_itself() -> None:
    source = make_repo(temp_root() / "fixture-repo" / "worktree-src")
    with Workspace.create(source, mode="worktree") as workspace:
        assert workspace.mode is WorkspaceMode.worktree
        assert workspace.read_file("CLAUDE.md") == "root instructions\n"
        path = workspace.path
        listed = subprocess.run(
            ["git", "-C", str(source), "worktree", "list"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert str(path) in listed

    assert not path.exists()
    remaining = subprocess.run(
        ["git", "-C", str(source), "worktree", "list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(path) not in remaining


def test_worktree_mode_needs_a_git_repository(tmp_path: Path) -> None:
    plain = temp_root() / "fixture-repo" / "not-a-repo"
    plain.mkdir(parents=True, exist_ok=True)
    with pytest.raises(WorkspaceError, match="needs a git repository"):
        Workspace.create(plain, mode="worktree")


def test_workspace_paths_are_unique_and_named_on_request() -> None:
    first = Workspace.create()
    second = Workspace.create(name="explicit-name")
    try:
        assert first.path != second.path
        assert second.path.name == "explicit-name"
        assert second.home.name == "explicit-name-home"
        with pytest.raises(WorkspaceError, match="already exists"):
            Workspace.create(name="explicit-name")
    finally:
        first.close()
        second.close()
    # Once closed the name is free again.
    with Workspace.create(name="explicit-name") as reused:
        assert reused.path.name == "explicit-name"
