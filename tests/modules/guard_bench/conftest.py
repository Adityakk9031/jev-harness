"""Helpers that build throwaway projects with known guards."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import fixture_path

HOOKS = fixture_path("guard", "hooks")


def hook_command(name: str) -> str:
    return f"{sys.executable} {HOOKS / name}"


MakeProject = Callable[..., Path]


def build_project(
    root: Path,
    name: str,
    *,
    hooks: list[str] | None = None,
    deny: list[str] | None = None,
    ask: list[str] | None = None,
    allow: list[str] | None = None,
    matcher: str = "*",
    extra: dict[str, object] | None = None,
) -> Path:
    project = root / name
    (project / ".claude").mkdir(parents=True)
    settings: dict[str, object] = dict(extra or {})
    permissions = {
        key: value for key, value in (("deny", deny), ("ask", ask), ("allow", allow)) if value
    }
    if permissions:
        settings["permissions"] = permissions
    if hooks:
        settings["hooks"] = {
            "PreToolUse": [
                {
                    "matcher": matcher,
                    "hooks": [{"type": "command", "command": hook_command(h)} for h in hooks],
                }
            ]
        }
    (project / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    return project


@pytest.fixture
def make_project(tmp_path: Path) -> MakeProject:
    """``make_project(hooks=[...], deny=[...], matcher="*")`` -> a project dir."""
    counter = {"n": 0}

    def build(**kwargs: Any) -> Path:
        counter["n"] += 1
        return build_project(tmp_path, f"project-{counter['n']}", **kwargs)

    return build
