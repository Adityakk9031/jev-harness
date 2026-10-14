"""Settings: defaults, then file, then environment, then CLI overrides.

Hand-rolled rather than pydantic-settings (PLAN section 7) because the mapping
from environment variable to field is explicit and short, and because it lets
us guarantee one thing in code: secret values live in :class:`SecretStr`, and
:meth:`Settings.redacted` is the only way settings are ever rendered. An empty
value is treated as unset, so a sourced ``.env`` with blank keys cannot create
an empty credential.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from jev_harness.core.errors import ConfigError

DEFAULT_JEV_API_URL = "https://api.typesafe.ai/v1"
DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_CONFIG_FILENAMES = ("jev-harness.yml", "jev-harness.yaml")


class JudgeBackend(StrEnum):
    """Which judge a module should use to add findings or adjust confidence."""

    none = "none"
    jev = "jev"
    anthropic = "anthropic"
    openai = "openai"


class FailOn(StrEnum):
    """The severity at which a command should exit non-zero."""

    flag = "flag"
    escalate = "escalate"


#: Environment variable names per field. The first present, non-empty name wins.
ENV_FIELDS: dict[str, tuple[str, ...]] = {
    "judge": ("JEV_JUDGE",),
    "jev_api_key": ("JEV_API_KEY", "TYPESAFE_API_KEY"),
    "jev_api_url": ("JEV_API_URL",),
    "jev_model": ("JEV_MODEL",),
    "anthropic_api_key": ("ANTHROPIC_API_KEY",),
    "openai_api_key": ("OPENAI_API_KEY",),
    "github_token": ("GITHUB_TOKEN",),
    "judge_timeout_seconds": ("JEV_JUDGE_TIMEOUT_SECONDS",),
    "judge_retries": ("JEV_JUDGE_RETRIES",),
    "min_judge_confidence": ("JEV_MIN_JUDGE_CONFIDENCE",),
    "min_samples": ("JEV_MIN_SAMPLES",),
    "min_events": ("JEV_MIN_EVENTS",),
    "max_cost_usd": ("JEV_MAX_COST_USD",),
    "max_runs": ("JEV_MAX_RUNS",),
    "fail_on": ("JEV_FAIL_ON",),
    "output_dir": ("JEV_OUTPUT_DIR",),
}

SECRET_FIELDS = frozenset({"jev_api_key", "anthropic_api_key", "openai_api_key", "github_token"})


class Settings(BaseModel):
    """Resolved configuration for one invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judge: JudgeBackend = JudgeBackend.none
    jev_api_key: SecretStr | None = None
    jev_api_url: str = DEFAULT_JEV_API_URL
    jev_model: str = DEFAULT_JEV_MODEL
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    judge_timeout_seconds: float = Field(default=10.0, gt=0.0)
    judge_retries: int = Field(default=2, ge=0, le=10)
    #: Judge confidence below this escalates instead of passing (brief 2.6).
    min_judge_confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    #: Sample-size policy for every reported rate (see core.stats).
    min_samples: int = Field(default=30, ge=1)
    min_events: int = Field(default=5, ge=0)

    max_cost_usd: float | None = Field(default=None, gt=0.0)
    max_runs: int | None = Field(default=None, ge=1)
    fail_on: FailOn = FailOn.flag
    output_dir: Path = Path(".jev-harness")

    @property
    def judge_enabled(self) -> bool:
        return self.judge is not JudgeBackend.none

    def judge_credential(self) -> SecretStr | None:
        """The credential for the selected judge backend, if any."""
        match self.judge:
            case JudgeBackend.jev:
                return self.jev_api_key
            case JudgeBackend.anthropic:
                return self.anthropic_api_key
            case JudgeBackend.openai:
                return self.openai_api_key
            case _:
                return None

    def redacted(self) -> dict[str, Any]:
        """A rendering-safe view: secrets become a presence marker, never a value."""
        out: dict[str, Any] = {}
        for name, value in self:
            if isinstance(value, SecretStr):
                out[name] = "<set>" if value.get_secret_value() else None
            elif isinstance(value, Path):
                out[name] = str(value)
            elif isinstance(value, StrEnum):
                out[name] = value.value
            else:
                out[name] = value
        return out


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path} must contain a mapping of setting names to values, got {type(raw).__name__}"
        )
    for key in raw:
        if not isinstance(key, str):
            raise ConfigError(f"{path} has a non-string key: {key!r}")
    return dict(raw)


def _from_env(env: Mapping[str, str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for field, names in ENV_FIELDS.items():
        for name in names:
            value = env.get(name)
            if value is not None and value.strip():
                resolved[field] = value.strip()
                break
    return resolved


def read_env_file(path: Path) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from a file the user named, keeping only variables we know.

    Unknown variables are ignored rather than imported, so pointing this at a shared ``.env``
    cannot smuggle anything else into the process. Values are never echoed anywhere.
    """
    known = {name for names in ENV_FIELDS.values() for name in names}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read env file {path}: {exc}") from exc
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in known and value:
            values[key] = value
    return values


def resolve_config_path(config_path: Path | None, cwd: Path | None = None) -> Path | None:
    """Find the config file: explicit path must exist, otherwise search ``cwd``."""
    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        return config_path
    base = Path.cwd() if cwd is None else cwd
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def load_settings(
    config_path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
    cwd: Path | None = None,
) -> Settings:
    """Build settings with precedence: defaults < file < env < overrides."""
    environment = os.environ if env is None else env
    path = resolve_config_path(config_path, cwd)

    data: dict[str, Any] = {}
    if path is not None:
        data.update(_read_config_file(path))
    data.update(_from_env(environment))
    if overrides is not None:
        data.update({key: value for key, value in overrides.items() if value is not None})

    try:
        return Settings(**data)
    except ValidationError as exc:
        raise ConfigError(_describe_validation_error(exc, path)) from exc


def _describe_validation_error(exc: ValidationError, path: Path | None) -> str:
    source = f" ({path})" if path is not None else ""
    problems = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return f"invalid setting{source}: {problems}"
