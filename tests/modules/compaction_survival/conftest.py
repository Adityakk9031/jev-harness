from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

MakeClaudeProject = Callable[..., Path]


@pytest.fixture
def claude_project(tmp_path: Path) -> Path:
    """A project exercising every Claude Code source kind the module probes."""
    project = tmp_path / "proj"
    (project / ".claude" / "rules").mkdir(parents=True)
    (project / "sub").mkdir()
    (project / "src").mkdir()
    (project / "CLAUDE.md").write_text("# Project\nAlways run the tests.\n@docs/extra.md\n")
    (project / "docs").mkdir()
    (project / "docs" / "extra.md").write_text("Imported guidance.\n")
    (project / "sub" / "CLAUDE.md").write_text("Rules for the sub package.\n")
    (project / ".claude" / "rules" / "style.md").write_text("Be terse.\n")
    (project / ".claude" / "rules" / "py.md").write_text(
        '---\npaths:\n  - "src/**/*.py"\n---\nUse type hints.\n'
    )
    (project / "src" / "app.py").write_text("print('hi')\n")
    return project
