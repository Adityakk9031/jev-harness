"""The composite action: structure, input validation, and (live) an end-to-end local run."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ACTION = Path(__file__).parent.parent / "action" / "action.yml"


def load() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def script() -> str:
    steps = load()["runs"]["steps"]
    return next(s["run"] for s in steps if s.get("id") == "run")


def test_action_is_a_composite_action_with_the_documented_inputs() -> None:
    action = load()
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {
        "command", "args", "fail-on", "package", "python-version", "working-directory",
    }  # fmt: skip
    assert action["inputs"]["command"]["required"] is True
    assert action["inputs"]["fail-on"]["default"] == "flag"
    assert action["outputs"]["report"]["value"] == "${{ steps.run.outputs.report }}"


def test_inputs_reach_the_script_only_through_the_environment() -> None:
    """A pull request that edits `args` must not be able to inject shell into the run block."""
    assert "${{" not in script()
    env = next(s for s in load()["runs"]["steps"] if s.get("id") == "run")["env"]
    assert all(value.startswith("${{ inputs.") for value in env.values())


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (
            {"JEV_ACTION_COMMAND": "compaction", "JEV_ACTION_FAIL_ON": "flag"},
            "must be guard or calibrate",
        ),
        (
            {"JEV_ACTION_COMMAND": "guard; rm -rf /", "JEV_ACTION_FAIL_ON": "flag"},
            "must be guard or calibrate",
        ),
        (
            {"JEV_ACTION_COMMAND": "guard", "JEV_ACTION_FAIL_ON": "never"},
            "must be flag or escalate",
        ),
    ],
)
def test_script_rejects_bad_inputs_before_installing_anything(
    env: dict[str, str], message: str, tmp_path: Path
) -> None:
    result = subprocess.run(
        ["bash", "-c", script()],
        env={**os.environ, "JEV_ACTION_ARGS": "", "JEV_ACTION_PACKAGE": "x", "JEV_ACTION_PYTHON": "3.11", **env},
        capture_output=True, text=True, cwd=tmp_path, check=False,
    )  # fmt: skip
    assert result.returncode == 2
    assert message in result.stdout


@pytest.mark.live
@pytest.mark.skipif(shutil.which("uvx") is None, reason="uvx is required")
def test_script_runs_end_to_end_against_this_checkout(tmp_path: Path) -> None:
    """Runs the action's real script with uvx, installing this checkout. Needs network for deps."""
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text('{"permissions": {"deny": ["Bash(rm:*)"]}}')
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-c", script()],
        env={
            **os.environ,
            "JEV_ACTION_COMMAND": "guard",
            "JEV_ACTION_ARGS": "run --no-user-settings --no-managed-settings",
            "JEV_ACTION_FAIL_ON": "flag",
            "JEV_ACTION_PACKAGE": str(Path(__file__).parent.parent),
            "JEV_ACTION_PYTHON": "3.11",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(tmp_path / "out.txt"),
        },
        capture_output=True, text=True, cwd=project, check=False, timeout=600,
    )  # fmt: skip
    assert result.returncode == 1, result.stdout + result.stderr  # the bench finds bypasses
    assert "guard_bench" in summary.read_text()
    assert (tmp_path / "jev-harness-report.json").is_file()
    assert "report=" in (tmp_path / "out.txt").read_text()
