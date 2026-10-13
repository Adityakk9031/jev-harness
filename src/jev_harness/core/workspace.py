"""Disposable copies of a project - the only place jev-harness writes.

The first product promise is that nothing here ever touches the user's real
setup. That is enforced structurally rather than by discipline:

* Every workspace lives under one process-wide temp root, and
  :func:`_assert_disposable` refuses any path outside it, so a module cannot
  edit a real instruction file even by accident.
* ``create`` also refuses a source that *is* the current working tree, which
  catches the "I forgot to pass a temp dir" mistake at the start rather than
  after the damage.
* ``plant_file`` rejects absolute paths and ``..`` escapes, so a corpus entry
  cannot write outside its workspace.
* Cleanup is guaranteed twice: by the context manager, and by an ``atexit``
  sweep of the temp root if a module crashes.

``mode="copy"`` is the default because ``mode="worktree"`` necessarily writes
metadata into the user's ``.git`` directory (and is cleaned up again on close).
"""

from __future__ import annotations

import atexit
import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from jev_harness.core.errors import HarnessError

#: Never copied into a workspace unless explicitly requested.
DEFAULT_EXCLUDES = (".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache")

_TEMP_ROOT: Path | None = None


class WorkspaceError(HarnessError):
    """A workspace could not be created, or an unsafe path was refused."""


class WorkspaceMode(StrEnum):
    """How the workspace was produced."""

    copy = "copy"
    worktree = "worktree"
    empty = "empty"


def temp_root() -> Path:
    """The process-wide temp root, created once and swept at exit."""
    global _TEMP_ROOT
    if _TEMP_ROOT is None or not _TEMP_ROOT.is_dir():
        _TEMP_ROOT = Path(tempfile.mkdtemp(prefix="jev-harness-"))
        atexit.register(shutil.rmtree, _TEMP_ROOT, ignore_errors=True)
    return _TEMP_ROOT


def _assert_disposable(path: Path) -> None:
    resolved = path.resolve()
    root = temp_root().resolve()
    if not resolved.is_relative_to(root):
        raise WorkspaceError(
            f"refusing to write to {resolved}: jev-harness only writes inside its own "
            f"disposable workspace under {root}, never your real tree"
        )


def _assert_not_live_tree(source: Path) -> None:
    """Refuse a source that is (or contains) the current working directory."""
    resolved = source.resolve()
    cwd = Path.cwd().resolve()
    if resolved == cwd or cwd.is_relative_to(resolved):
        raise WorkspaceError(
            f"refusing to use {resolved} as a workspace: it contains the current working "
            "directory. jev-harness must never operate inside your live checkout; pass a "
            "temporary copy or leave the default of no source."
        )


@dataclass
class Workspace:
    """A disposable project copy plus its own empty ``HOME``.

    ``home`` exists because guard benches run user hook scripts with a
    redirected ``HOME`` so that a hook configured at user scope is not the one
    under test unless the caller copies it in deliberately.
    """

    path: Path
    home: Path
    mode: WorkspaceMode
    source: Path | None = None
    planted: list[str] = field(default_factory=list)
    _closed: bool = False

    # ------------------------------------------------------------- creation

    @classmethod
    def create(
        cls,
        source: Path | None = None,
        *,
        mode: WorkspaceMode | str | None = None,
        include_git: bool = False,
        name: str | None = None,
    ) -> Workspace:
        """Create a workspace: a copy of ``source``, a git worktree, or empty.

        With no ``mode``: ``source`` given means copy it, otherwise the
        workspace starts empty.
        """
        resolved_mode = (
            (WorkspaceMode.empty if source is None else WorkspaceMode.copy)
            if mode is None
            else (mode if isinstance(mode, WorkspaceMode) else WorkspaceMode(mode))
        )
        if resolved_mode is WorkspaceMode.empty and source is not None:
            raise WorkspaceError("mode='empty' takes no source")

        root = temp_root()
        directory_name = name or f"ws-{uuid4().hex[:10]}"
        path = root / directory_name
        home = root / f"{directory_name}-home"

        if path.exists():
            raise WorkspaceError(f"workspace path already exists: {path}")

        if resolved_mode is WorkspaceMode.empty:
            path.mkdir(parents=True)
        else:
            if source is None:
                raise WorkspaceError(
                    f"mode={resolved_mode.value!r} needs a source directory; pass a source path "
                    "or use the default empty workspace"
                )
            source = source.expanduser().resolve()
            if not source.is_dir():
                raise WorkspaceError(f"source is not a directory: {source}")
            _assert_not_live_tree(source)
            if resolved_mode is WorkspaceMode.copy:
                _copy_tree(source, path, include_git=include_git)
            else:
                _add_worktree(source, path)

        home.mkdir(parents=True, exist_ok=True)
        # Store resolved paths: on macOS /var is a symlink to /private/var, so an
        # unresolved root and a resolved child would fail a containment check
        # even though they point at the same directory.
        path = path.resolve()
        home = home.resolve()
        _assert_disposable(path)
        return cls(path=path, home=home, mode=resolved_mode, source=source)

    # -------------------------------------------------------------- content

    def plant_file(self, relative: str | Path, content: str) -> Path:
        """Write a file into the workspace, refusing to escape it."""
        target = self.resolve(relative)
        # Guard again here, not only in create(): a Workspace built by hand (or
        # with a path from a caller) must not be able to write into a real tree.
        _assert_disposable(self.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        key = str(target.relative_to(self.path))
        if key not in self.planted:
            self.planted.append(key)
        return target

    def plant_files(self, files: dict[str, str]) -> list[Path]:
        """Plant several files; later entries win on conflicting paths."""
        return [self.plant_file(name, content) for name, content in files.items()]

    def resolve(self, relative: str | Path) -> Path:
        """Resolve ``relative`` inside the workspace, refusing escapes."""
        raw = Path(relative)
        if raw.is_absolute():
            raise WorkspaceError(f"planted paths must be relative to the workspace: {raw}")
        root = self.path.resolve()
        target = (root / raw).resolve()
        if not target.is_relative_to(root):
            raise WorkspaceError(
                f"{raw} escapes the workspace: a planted file may not write outside {root}"
            )
        return target

    def read_file(self, relative: str | Path) -> str:
        return self.resolve(relative).read_text(encoding="utf-8")

    def exists(self, relative: str | Path) -> bool:
        return self.resolve(relative).exists()

    def list_files(self, *, include_dot_dirs: bool = False) -> list[str]:
        """Workspace-relative files, sorted; ``.git`` internals are excluded."""
        found: list[str] = []
        for path in sorted(self.path.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.path)
            if not include_dot_dirs and relative.parts and relative.parts[0] == ".git":
                continue
            found.append(str(relative))
        return found

    def differs_from_source(self) -> list[str]:
        """Files that differ from the source, plus added and removed paths.

        Only meaningful for copy workspaces; a worktree starts clean against
        the commit it was created from, and an empty workspace has no source.
        """
        if self.source is None:
            return []
        source_files = _relative_files(self.source)
        workspace_files = set(self.list_files())
        changed: list[str] = []
        for relative in sorted(workspace_files | set(source_files)):
            if relative in self.planted:
                continue
            if (
                relative not in source_files
                or relative not in workspace_files
                or _digest(self.path / relative) != _digest(self.source / relative)
            ):
                changed.append(relative)
        return changed

    # -------------------------------------------------------------- cleanup

    def close(self) -> None:
        """Remove the workspace. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        if self.mode is WorkspaceMode.worktree and self.source is not None:
            _remove_worktree(self.source, self.path)
        shutil.rmtree(self.path, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _copy_tree(source: Path, target: Path, *, include_git: bool) -> None:
    ignore = None if include_git else shutil.ignore_patterns(*DEFAULT_EXCLUDES)
    try:
        shutil.copytree(source, target, symlinks=True, ignore=ignore)
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise WorkspaceError(f"could not copy {source} to {target}: {exc}") from exc


def _add_worktree(source: Path, target: Path) -> None:
    if not (source / ".git").exists():
        raise WorkspaceError(f"mode='worktree' needs a git repository, but {source} has no .git")
    completed = subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "--detach", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-300:]
        raise WorkspaceError(
            f"git worktree add failed for {source} (exit {completed.returncode})"
            + (f": {tail}" if tail else "")
        )


def _remove_worktree(source: Path, target: Path) -> None:
    subprocess.run(
        ["git", "-C", str(source), "worktree", "remove", "--force", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(source), "worktree", "prune"],
        capture_output=True,
        text=True,
        check=False,
    )


def _relative_files(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in DEFAULT_EXCLUDES:
            continue
        found.add(str(relative))
    return found


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "<unreadable>"
