from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.modules.guard_bench.conftest import MakeProject

from jev_harness.core.errors import ConfigError
from jev_harness.modules.guard_bench.discovery import discover


def test_reads_rules_and_hooks_from_project_settings(make_project: MakeProject) -> None:
    project = make_project(
        hooks=["naive_guard.py"],
        deny=["Bash(rm:*)"],
        ask=["Bash(git push:*)"],
        allow=["Bash(ls:*)"],
    )
    config = discover(project, include_managed=False)
    assert {(r.kind, r.spec) for r in config.rules} == {
        ("deny", "Bash(rm:*)"),
        ("ask", "Bash(git push:*)"),
        ("allow", "Bash(ls:*)"),
    }
    assert len(config.hooks) == 1
    assert config.hooks[0].matcher == "*"
    assert config.has_guards
    assert config.warnings == []


def test_user_and_local_settings_are_merged_in_order(
    tmp_path: Path, make_project: MakeProject
) -> None:
    project = make_project(deny=["Bash(rm:*)"])
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"deny": ["Read(./.env)"]}}), encoding="utf-8"
    )
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(sudo:*)"]}}), encoding="utf-8"
    )
    config = discover(project, home=home, include_managed=False)
    assert [r.spec for r in config.rules] == ["Bash(sudo:*)", "Bash(rm:*)", "Read(./.env)"]
    assert len(config.settings_files) == 3
    without_user = discover(project, home=None, include_managed=False)
    assert "Bash(sudo:*)" not in [r.spec for r in without_user.rules]


def test_no_guards_is_reported_not_silent(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    config = discover(project, include_managed=False)
    assert not config.has_guards
    assert any("no permission rules" in w for w in config.warnings)


def test_disable_all_hooks_removes_settings_hooks_but_not_generic_guards(
    make_project: MakeProject,
) -> None:
    project = make_project(hooks=["naive_guard.py"], extra={"disableAllHooks": True})
    config = discover(project, include_managed=False, generic_guards=["./my-guard"])
    assert [h.origin for h in config.hooks] == ["generic"]
    assert any("disableAllHooks" in w for w in config.warnings)


def test_non_command_hooks_are_skipped_with_a_warning(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "x"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    config = discover(project, include_managed=False)
    assert config.hooks == []
    assert any("type 'prompt'" in w for w in config.warnings)


@pytest.mark.parametrize("body", ["{not json", "[1]"])
def test_unreadable_settings_fail_loudly(tmp_path: Path, body: str) -> None:
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        discover(project, include_managed=False)


def test_project_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not a directory"):
        discover(tmp_path / "missing")


def test_config_hash_tracks_rules_hooks_and_hook_script_contents(
    tmp_path: Path, make_project: MakeProject
) -> None:
    script = tmp_path / "guard.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    project = make_project(deny=["Bash(rm:*)"])
    first = discover(project, include_managed=False, generic_guards=[f"sh {script}"]).sha()
    again = discover(project, include_managed=False, generic_guards=[f"sh {script}"]).sha()
    assert first == again
    script.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    changed = discover(project, include_managed=False, generic_guards=[f"sh {script}"]).sha()
    assert changed != first
