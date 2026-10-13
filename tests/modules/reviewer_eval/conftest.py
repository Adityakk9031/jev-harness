"""A real git repository with known history, built with the git CLI."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ENV = {
    "GIT_AUTHOR_NAME": "Dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}

BUGGY = """def clamp(value, low, high):
    if value < low:
        return low
    if value > high + 1:
        return high
    return value
"""
FIXED = BUGGY.replace("high + 1", "high")


def run(cwd: Path, *args: str, **kwargs: str) -> str:
    import os

    env = {**os.environ, **ENV, **kwargs}
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )
    return done.stdout.strip()


@dataclass
class Repo:
    path: Path
    fix: str  # the bug fix commit
    refactor: str  # a neutral small change with tests
    big: str  # a change too large to be a candidate
    revert: str


def commit(path: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    run(path, "add", "-A")
    run(path, "commit", "-q", "-m", message)
    return run(path, "rev-parse", "HEAD")


@pytest.fixture
def source_repo(tmp_path: Path) -> Repo:
    path = tmp_path / "app"
    path.mkdir()
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "commit.gpgsign", "false")
    commit(
        path,
        "Initial import",
        {
            "src/calc.py": BUGGY,
            "tests/test_calc.py": "from src.calc import clamp\n\n\ndef test_low():\n    assert clamp(0, 1, 5) == 1\n",
            "README.md": "# app\n",
        },
    )
    commit(path, "Add docs", {"docs/guide.md": "guide\n"})
    fix = commit(
        path,
        "Fix off-by-one in clamp (#12)",
        {
            "src/calc.py": FIXED,
            "tests/test_calc.py": "from src.calc import clamp\n\n\ndef test_low():\n    assert clamp(0, 1, 5) == 1\n\n\ndef test_high():\n    assert clamp(6, 1, 5) == 5\n",
        },
    )
    refactor = commit(
        path,
        "Tidy helper",
        {
            "src/util.py": "def double(x):\n    return x * 2\n",
            "tests/test_util.py": "from src.util import double\n\n\ndef test_double():\n    assert double(2) == 4\n",
        },
    )
    big = commit(
        path,
        "Fix everything",
        {
            "src/big.py": "\n".join(f"x{i} = {i}" for i in range(80)) + "\n",
            "tests/test_big.py": "def test_big():\n    assert True\n",
        },
    )
    revert = commit(
        path,
        'Revert "Tidy helper"',
        {
            "src/util.py": "def double(x):\n    return x + x\n",
            "tests/test_util.py": "def test_x():\n    assert True\n",
        },
    )
    return Repo(path, fix, refactor, big, revert)


@pytest.fixture
def bare_target(tmp_path: Path) -> Path:
    path = tmp_path / "target.git"
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
    return path
