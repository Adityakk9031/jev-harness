"""Settings tests: precedence, validation, and - most importantly - secrecy."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from jev_harness.core.config import (
    DEFAULT_JEV_API_URL,
    DEFAULT_JEV_MODEL,
    FailOn,
    JudgeBackend,
    Settings,
    load_settings,
    resolve_config_path,
)
from jev_harness.core.errors import ConfigError

SECRET = "sk-jev-do-not-leak-0123456789"


def test_defaults_need_no_configuration(tmp_path: Path) -> None:
    settings = load_settings(env={}, cwd=tmp_path)
    assert settings.judge is JudgeBackend.none
    assert settings.judge_enabled is False
    assert settings.jev_api_url == DEFAULT_JEV_API_URL
    assert settings.jev_model == DEFAULT_JEV_MODEL
    assert settings.min_samples == 30
    assert settings.min_events == 5
    assert settings.min_judge_confidence == 0.7
    assert settings.fail_on is FailOn.flag
    assert settings.max_cost_usd is None
    assert settings.max_runs is None
    assert settings.output_dir == Path(".jev-harness")


def test_environment_overrides_defaults(tmp_path: Path) -> None:
    settings = load_settings(
        env={"JEV_JUDGE": "jev", "JEV_API_KEY": SECRET, "JEV_MIN_SAMPLES": "7"},
        cwd=tmp_path,
    )
    assert settings.judge is JudgeBackend.jev
    assert settings.min_samples == 7
    assert settings.judge_credential() is not None
    assert settings.judge_credential().get_secret_value() == SECRET  # type: ignore[union-attr]


def test_blank_environment_values_are_treated_as_unset(tmp_path: Path) -> None:
    settings = load_settings(env={"JEV_API_KEY": "   ", "TYPESAFE_API_KEY": ""}, cwd=tmp_path)
    assert settings.jev_api_key is None


def test_typesafe_api_key_alias_and_precedence(tmp_path: Path) -> None:
    alias_only = load_settings(env={"TYPESAFE_API_KEY": "alias-key"}, cwd=tmp_path)
    assert alias_only.jev_api_key is not None
    assert alias_only.jev_api_key.get_secret_value() == "alias-key"

    both = load_settings(env={"TYPESAFE_API_KEY": "alias-key", "JEV_API_KEY": SECRET}, cwd=tmp_path)
    assert both.jev_api_key is not None
    assert both.jev_api_key.get_secret_value() == SECRET


def test_config_file_is_discovered_in_cwd(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("min_samples: 50\nfail_on: escalate\n")
    settings = load_settings(env={}, cwd=tmp_path)
    assert settings.min_samples == 50
    assert settings.fail_on is FailOn.escalate
    assert resolve_config_path(None, tmp_path) == tmp_path / "jev-harness.yml"


def test_environment_beats_file_and_overrides_beat_both(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("min_samples: 50\n")
    from_file = load_settings(env={}, cwd=tmp_path)
    from_env = load_settings(env={"JEV_MIN_SAMPLES": "10"}, cwd=tmp_path)
    overridden = load_settings(
        env={"JEV_MIN_SAMPLES": "10"}, overrides={"min_samples": 3}, cwd=tmp_path
    )
    assert (from_file.min_samples, from_env.min_samples, overridden.min_samples) == (50, 10, 3)


def test_none_overrides_do_not_clobber_settings(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("min_samples: 42\n")
    settings = load_settings(env={}, overrides={"min_samples": None}, cwd=tmp_path)
    assert settings.min_samples == 42


def test_explicit_config_path_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_settings(tmp_path / "nope.yml", env={}, cwd=tmp_path)


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("min_sampels: 50\n")
    with pytest.raises(ConfigError, match="min_sampels"):
        load_settings(env={}, cwd=tmp_path)


def test_config_file_must_be_a_mapping(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("- just\n- a list\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(env={}, cwd=tmp_path)


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    (tmp_path / "jev-harness.yml").write_text("min_samples: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_settings(env={}, cwd=tmp_path)


def test_invalid_values_name_the_setting(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="min_samples"):
        load_settings(env={"JEV_MIN_SAMPLES": "many"}, cwd=tmp_path)
    with pytest.raises(ConfigError, match="judge"):
        load_settings(env={"JEV_JUDGE": "gpt-ish"}, cwd=tmp_path)


def test_secrets_never_render() -> None:
    settings = Settings(
        judge=JudgeBackend.jev,
        jev_api_key=SecretStr(SECRET),
        anthropic_api_key=SecretStr("anthropic-key"),
        github_token=SecretStr("ghp_token"),
    )
    redacted = settings.redacted()
    serialized = json.dumps(redacted, sort_keys=True)
    assert SECRET not in serialized
    assert "anthropic-key" not in serialized
    assert "ghp_token" not in serialized
    assert SECRET not in repr(settings)
    assert redacted["jev_api_key"] == "<set>"
    assert redacted["openai_api_key"] is None
    assert redacted["judge"] == "jev"
    assert redacted["output_dir"] == ".jev-harness"


def test_judge_credential_matches_the_selected_backend() -> None:
    settings = Settings(
        judge=JudgeBackend.openai,
        jev_api_key=SecretStr("jev-key"),
        openai_api_key=SecretStr("openai-key"),
    )
    credential = settings.judge_credential()
    assert credential is not None
    assert credential.get_secret_value() == "openai-key"
    assert Settings(judge=JudgeBackend.none, jev_api_key=SecretStr("x")).judge_credential() is None


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.min_samples = 5
