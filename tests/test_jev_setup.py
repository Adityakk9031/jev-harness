"""Setting up Jev: --env-file and `judge check`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import respx
from tests.judges import install_fake_jev
from typer.testing import CliRunner

from jev_harness.cli import app
from jev_harness.core.config import read_env_file
from jev_harness.core.errors import ConfigError

runner = CliRunner()


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "jev.env"
    path.write_text(text, encoding="utf-8")
    return path


def test_env_file_keeps_known_variables_and_ignores_the_rest(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "# comment\n\nJEV_API_KEY='abc123'\nexport JEV_MODEL=\"jev-latest\"\nAWS_SECRET=zzz\n"
        "JEV_API_URL=\nnot a pair\nGITHUB_TOKEN = ghp_x\n",
    )
    assert read_env_file(path) == {
        "JEV_API_KEY": "abc123",
        "JEV_MODEL": "jev-latest",
        "GITHUB_TOKEN": "ghp_x",
    }  # AWS_SECRET is not ours, and an empty value is treated as unset


def test_a_missing_env_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read env file"):
        read_env_file(tmp_path / "nope.env")


def test_env_file_feeds_settings_and_the_real_environment_wins(tmp_path: Path) -> None:
    path = write(tmp_path, "JEV_API_KEY=secret-key-value\nJEV_MODEL=model-from-file\n")
    shown = runner.invoke(
        app, ["--env-file", str(path), "config", "show", "--json"], env={"JEV_API_KEY": ""}
    )
    assert shown.exit_code == 0, shown.output
    assert '"jev_api_key": "<set>"' in shown.output  # present, and its value is never printed
    assert "secret-key-value" not in shown.output
    assert '"jev_model": "model-from-file"' in shown.output

    wins = runner.invoke(
        app,
        ["--env-file", str(path), "config", "show", "--json"],
        env={"JEV_MODEL": "model-from-env"},
    )
    assert '"jev_model": "model-from-env"' in wins.output


def test_judge_check_reports_the_answer_and_latency(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    path = write(tmp_path, "JEV_API_KEY=file-key\n")
    with respx.mock(assert_all_called=False) as router:
        install_fake_jev(router, lambda q: ("yes", 0.97), calls)
        result = runner.invoke(
            app, ["--env-file", str(path), "judge", "check"], env={"JEV_API_KEY": ""}
        )
    assert result.exit_code == 0, result.output
    assert "judge:      jev (model jev-latest)" in result.output
    assert "answer:     yes" in result.output
    assert "confidence: 0.97" in result.output
    assert "ok: the key and endpoint work" in result.output
    assert len(calls) == 1


def test_judge_check_fails_clearly_without_a_key_or_when_the_service_is_down() -> None:
    no_key = runner.invoke(app, ["judge", "check"], env={"JEV_API_KEY": ""})
    assert no_key.exit_code == 2
    assert "--env-file" in no_key.output
    with respx.mock(assert_all_called=False) as router:
        install_fake_jev(router, lambda q: None, [])
        down = runner.invoke(app, ["judge", "check"], env={"JEV_API_KEY": "k"})
    assert down.exit_code == 2
    assert "did not answer" in down.output
    assert down.output.strip() != "k"  # the key itself is never echoed


def test_the_example_config_loads_and_makes_jev_the_default_judge() -> None:
    from jev_harness.core.config import JudgeBackend, load_settings

    example = Path(__file__).parent.parent / "jev-harness.example.yml"
    settings = load_settings(example, env={})
    assert settings.judge is JudgeBackend.jev
    assert settings.max_runs == 200
    assert settings.jev_api_key is None  # a key is never read from the file


def test_a_dot_env_in_the_current_folder_is_used_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("JEV_API_KEY=cwd-secret\nJEV_JUDGE=jev\nAWS_SECRET=zzz\n")
    monkeypatch.chdir(tmp_path)
    shown = runner.invoke(app, ["config", "show", "--json"], env={"JEV_API_KEY": ""})
    assert shown.exit_code == 0, shown.output
    assert '"jev_api_key": "<set>"' in shown.output
    assert '"judge": "jev"' in shown.output
    assert "cwd-secret" not in shown.output


def test_an_explicit_env_file_beats_the_dot_env_and_nothing_leaks_into_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    (tmp_path / ".env").write_text("JEV_MODEL=from-dot-env\n")
    other = tmp_path / "other.env"
    other.write_text("JEV_MODEL=from-explicit\n")
    monkeypatch.chdir(tmp_path)
    shown = runner.invoke(app, ["--env-file", str(other), "config", "show", "--json"])
    assert '"jev_model": "from-explicit"' in shown.output
    monkeypatch.delenv("JEV_MODEL", raising=False)
    runner.invoke(app, ["config", "show"])
    # Values are read into settings only; they are not exported, so hooks and subprocesses
    # started later never inherit the key.
    assert "JEV_MODEL" not in os.environ
