"""Find the guards that protect a project: permission rules and PreToolUse hooks.

Reads Claude Code settings only (PLAN D7); a generic ``--guard`` command covers
anything else that speaks the same stdin-JSON contract. Settings files are read,
never written.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import ConfigError

RuleKind = Literal["allow", "ask", "deny"]
MAX_SCRIPT_BYTES = 1_000_000


class PermissionRule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RuleKind
    spec: str
    source: str


class HookSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str
    matcher: str = "*"
    timeout: float | None = None
    source: str
    #: ``settings`` for hooks found in a settings file, ``generic`` for ``--guard`` commands.
    origin: Literal["settings", "generic"] = "settings"


class GuardConfig(BaseModel):
    """Everything discovered, with the warnings a reader needs to trust it."""

    model_config = ConfigDict(extra="forbid")

    project: str
    settings_files: list[str] = Field(default_factory=list)
    rules: list[PermissionRule] = Field(default_factory=list)
    hooks: list[HookSpec] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def has_guards(self) -> bool:
        return bool(self.rules or self.hooks)

    def sha(self) -> str:
        """Digest of the rules, hook commands, and the hook scripts they point at."""
        scripts: dict[str, str] = {}
        for hook in self.hooks:
            for token in _command_tokens(hook.command):
                resolved = _resolve_script(token, Path(self.project))
                if resolved is not None:
                    scripts[token] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        document = {
            "rules": sorted((r.kind, r.spec) for r in self.rules),
            "hooks": sorted((h.matcher, h.command) for h in self.hooks),
            "scripts": scripts,
        }
        blob = json.dumps(document, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _resolve_script(token: str, project: Path) -> Path | None:
    expanded = (
        token.replace("${CLAUDE_PROJECT_DIR}", str(project))
        .replace("$CLAUDE_PROJECT_DIR", str(project))
        .replace("~", str(Path.home()), 1)
        if token.startswith(("$", "~", "/", "."))
        else token
    )
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        if candidate.is_file() and candidate.stat().st_size <= MAX_SCRIPT_BYTES:
            return candidate
    except OSError:
        return None
    return None


def managed_settings_paths() -> list[Path]:
    """Where organisation-managed settings live on this platform."""
    if sys.platform == "darwin":
        return [Path("/Library/Application Support/ClaudeCode/managed-settings.json")]
    if sys.platform.startswith("linux"):
        return [Path("/etc/claude-code/managed-settings.json")]
    return []


def settings_paths(project: Path, home: Path | None, *, include_managed: bool) -> list[Path]:
    paths: list[Path] = []
    if home is not None:
        paths.append(home / ".claude" / "settings.json")
    paths.append(project / ".claude" / "settings.json")
    paths.append(project / ".claude" / "settings.local.json")
    if include_managed:
        paths.extend(managed_settings_paths())
    return paths


def _read_settings(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def _collect_hooks(data: dict[str, Any], source: str, config: GuardConfig) -> None:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return
    blocks = hooks.get("PreToolUse", [])
    if not isinstance(blocks, list):
        config.warnings.append(f"{source}: hooks.PreToolUse is not a list; ignored")
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        matcher = block.get("matcher") or "*"
        for entry in block.get("hooks", []):
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type", "command")
            command = entry.get("command")
            if kind != "command" or not isinstance(command, str) or not command.strip():
                config.warnings.append(
                    f"{source}: skipped a PreToolUse hook of type {kind!r}; "
                    "only command hooks can be exercised"
                )
                continue
            timeout = entry.get("timeout")
            config.hooks.append(
                HookSpec(
                    command=command,
                    matcher=str(matcher),
                    timeout=float(timeout) if isinstance(timeout, int | float) else None,
                    source=source,
                )
            )


def _collect_rules(data: dict[str, Any], source: str, config: GuardConfig) -> None:
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return
    for kind in ("allow", "ask", "deny"):
        for spec in permissions.get(kind, []) or []:
            if isinstance(spec, str) and spec.strip():
                config.rules.append(PermissionRule(kind=kind, spec=spec.strip(), source=source))


def discover(
    project: Path,
    *,
    home: Path | None = None,
    include_managed: bool = True,
    generic_guards: list[str] | None = None,
) -> GuardConfig:
    """Discover guards for ``project``. ``home=None`` skips user-level settings."""
    project = project.resolve()
    if not project.is_dir():
        raise ConfigError(f"project path is not a directory: {project}")
    config = GuardConfig(project=str(project))
    hooks_disabled_by: list[str] = []

    for path in settings_paths(project, home, include_managed=include_managed):
        data = _read_settings(path)
        if data is None:
            continue
        source = str(path)
        config.settings_files.append(source)
        _collect_rules(data, source, config)
        if data.get("disableAllHooks") is True:
            hooks_disabled_by.append(source)
        else:
            _collect_hooks(data, source, config)

    if hooks_disabled_by:
        config.warnings.append(
            f"disableAllHooks is true in {hooks_disabled_by}; the agent would run no hooks from "
            "settings, so none are exercised"
        )
        config.hooks = [h for h in config.hooks if h.origin == "generic"]

    for command in generic_guards or []:
        config.hooks.append(
            HookSpec(command=command, matcher="*", source="--guard", origin="generic")
        )

    if not config.has_guards:
        config.warnings.append(
            "no permission rules or PreToolUse hooks were found, so nothing stands between the "
            "agent and these payloads except the agent's own default prompts"
        )
    return config
