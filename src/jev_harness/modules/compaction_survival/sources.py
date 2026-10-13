"""Discover the instruction sources an agent loads, and how each one loads.

Discovery is read-only. It looks at the project only: user-level files such as
``~/.claude/CLAUDE.md`` are never read or planted in, because the bench works on a
disposable copy and must not depend on (or touch) the real home directory.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import ConfigError

SourceKind = Literal[
    "root",
    "local",
    "import",
    "rule",
    "scoped_rule",
    "nested",
    "agents_root",
    "agents_nested",
]

MAX_IMPORT_HOPS = 4
CODEX_COMBINED_LIMIT_BYTES = 32 * 1024
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".jev-harness",
    "dist",
    "build",
}
IMPORT_PATTERN = re.compile(r"(?<![\w`@/.])@((?:[\w.~-]+/)*[\w.~-]+\.[A-Za-z0-9]+)")

#: Whether Claude Code's documentation says this kind is re-injected after compaction.
#: ``None`` means the documentation does not say, so the simulated documented-behavior strategy
#: treats it as lost rather than assuming it survives.
DOCUMENTED_REINJECTED: dict[str, bool | None] = {
    "root": True,
    "rule": True,
    "local": None,
    "import": None,
    "scoped_rule": False,
    "nested": False,
    "agents_root": None,
    "agents_nested": None,
}

LOADS: dict[str, str] = {
    "root": "loaded at session start; documented as re-injected after compaction",
    "rule": "unscoped rule, loaded at session start; documented as re-injected after compaction",
    "local": "loaded at session start next to the root file; re-injection not documented",
    "import": "pulled in by an @import from a loaded file; re-injection not documented",
    "scoped_rule": "path-scoped rule, loaded when a matching file is read; documented as "
    "reloaded on demand, not re-injected",
    "nested": "nested CLAUDE.md, loaded when a file in its directory is read; documented as "
    "reloaded on demand, not re-injected",
    "agents_root": "AGENTS.md at the project root, concatenated into the session instructions",
    "agents_nested": "AGENTS.md in a subdirectory; Codex only loads the chain from the project "
    "root to the working directory",
}


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: SourceKind
    path: str
    loads: str
    #: A project-relative file whose reading makes the agent load this source, if it needs one.
    trigger_path: str | None = None
    #: Whether the harness must create ``trigger_path`` in the workspace copy.
    trigger_planted: bool = False
    #: For scoped rules, the ``paths:`` globs.
    globs: list[str] = Field(default_factory=list)
    documented_reinjected: bool | None = None
    size_bytes: int = 0


class Discovery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: Literal["claude-code", "codex"]
    project: str
    sources: list[Source]
    warnings: list[str] = Field(default_factory=list)
    #: Discovered but deliberately not probed in v1 (skills, subagents), with the reason.
    not_probed: list[str] = Field(default_factory=list)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _walk(project: Path, name: str) -> list[Path]:
    found: list[Path] = []
    for path in sorted(project.rglob(name)):
        relative = path.relative_to(project)
        if any(part in SKIP_DIRS for part in relative.parts[:-1]):
            continue
        if path.is_file():
            found.append(path)
    return found


def _unique(base: str, used: set[str]) -> str:
    candidate, counter = base, 2
    while candidate in used:
        candidate = f"{base}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split YAML frontmatter from a Markdown body. No frontmatter is an empty mapping."""
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if match is None:
        return {}, text
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML frontmatter: {exc}") from exc
    return (data if isinstance(data, dict) else {}), match.group(2)


def glob_to_probe_path(glob: str) -> str:
    """A concrete, plausible path that matches ``glob``, used as the file to read."""
    path = glob.removeprefix("./")
    path = path.replace("**/", "jev/").replace("**", "jev")
    path = re.sub(r"\{([^,}]+)[^}]*\}", r"\1", path)
    return path.replace("*", "probe").replace("?", "x")


def matches_glob(path: str, glob: str) -> bool:
    return fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob.replace("**/", ""))


def _discover_imports(root_file: Path, project: Path, used: set[str]) -> list[Source]:
    sources: list[Source] = []
    seen = {root_file.resolve()}
    frontier = [(root_file, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= MAX_IMPORT_HOPS:
            continue
        for match in IMPORT_PATTERN.finditer(current.read_text(encoding="utf-8", errors="replace")):
            target = (current.parent / match.group(1)).resolve()
            if target in seen or not target.is_file():
                continue
            try:
                relative = target.relative_to(project.resolve())
            except ValueError:
                continue
            seen.add(target)
            sources.append(
                Source(
                    id=_unique(f"import-{_slug(relative.stem)}", used),
                    kind="import",
                    path=str(relative),
                    loads=LOADS["import"],
                    documented_reinjected=DOCUMENTED_REINJECTED["import"],
                    size_bytes=target.stat().st_size,
                )
            )
            frontier.append((target, depth + 1))
    return sources


def discover_claude_code(project: Path) -> Discovery:
    project = project.resolve()
    if not project.is_dir():
        raise ConfigError(f"project path is not a directory: {project}")
    used: set[str] = set()
    sources: list[Source] = []
    warnings: list[str] = []
    not_probed: list[str] = []

    root_candidates = [project / "CLAUDE.md", project / ".claude" / "CLAUDE.md"]
    root_file = next((p for p in root_candidates if p.is_file()), None)
    if root_file is not None:
        sources.append(
            Source(
                id=_unique("root", used),
                kind="root",
                path=str(root_file.relative_to(project)),
                loads=LOADS["root"],
                documented_reinjected=DOCUMENTED_REINJECTED["root"],
                size_bytes=root_file.stat().st_size,
            )
        )
        sources.extend(_discover_imports(root_file, project, used))
    else:
        warnings.append("no CLAUDE.md at the project root or in .claude/")

    local = project / "CLAUDE.local.md"
    if local.is_file():
        sources.append(
            Source(
                id=_unique("local", used),
                kind="local",
                path="CLAUDE.local.md",
                loads=LOADS["local"],
                documented_reinjected=DOCUMENTED_REINJECTED["local"],
                size_bytes=local.stat().st_size,
            )
        )

    rules_dir = project / ".claude" / "rules"
    for rule in sorted(rules_dir.rglob("*.md")) if rules_dir.is_dir() else []:
        relative = rule.relative_to(project)
        meta, _ = parse_frontmatter(rule.read_text(encoding="utf-8", errors="replace"))
        raw_paths = meta.get("paths")
        if isinstance(raw_paths, str):
            globs = [raw_paths]
        elif isinstance(raw_paths, list):
            globs = [str(g) for g in raw_paths]
        else:
            globs = []
        if globs:
            probe = glob_to_probe_path(globs[0])
            sources.append(
                Source(
                    id=_unique(f"scoped-{_slug(rule.stem)}", used),
                    kind="scoped_rule",
                    path=str(relative),
                    loads=LOADS["scoped_rule"],
                    trigger_path=probe,
                    trigger_planted=not (project / probe).exists(),
                    globs=globs,
                    documented_reinjected=DOCUMENTED_REINJECTED["scoped_rule"],
                    size_bytes=rule.stat().st_size,
                )
            )
        else:
            sources.append(
                Source(
                    id=_unique(f"rule-{_slug(rule.stem)}", used),
                    kind="rule",
                    path=str(relative),
                    loads=LOADS["rule"],
                    documented_reinjected=DOCUMENTED_REINJECTED["rule"],
                    size_bytes=rule.stat().st_size,
                )
            )

    for nested in _walk(project, "CLAUDE.md"):
        relative = nested.relative_to(project)
        if root_file is not None and nested == root_file:
            continue
        if relative == Path("CLAUDE.md") or relative == Path(".claude/CLAUDE.md"):
            continue
        directory = relative.parent
        trigger = directory / ".jev-trigger.txt"
        sources.append(
            Source(
                id=_unique(f"nested-{_slug(str(directory))}", used),
                kind="nested",
                path=str(relative),
                loads=LOADS["nested"],
                trigger_path=str(trigger),
                trigger_planted=True,
                documented_reinjected=DOCUMENTED_REINJECTED["nested"],
                size_bytes=nested.stat().st_size,
            )
        )

    skills = sorted((project / ".claude" / "skills").glob("*/SKILL.md"))
    if skills:
        not_probed.append(
            f"{len(skills)} skill(s) under .claude/skills: loaded on invocation, and no verified "
            "way to invoke one headlessly yet"
        )
    agents_dir = project / ".claude" / "agents"
    subagents = sorted(agents_dir.glob("*.md")) if agents_dir.is_dir() else []
    if subagents:
        not_probed.append(
            f"{len(subagents)} subagent definition(s) under .claude/agents: they run in their own "
            "context, so main-session compaction does not apply to them"
        )

    return Discovery(
        agent="claude-code",
        project=str(project),
        sources=sources,
        warnings=warnings,
        not_probed=not_probed,
    )


def discover_codex(project: Path) -> Discovery:
    project = project.resolve()
    if not project.is_dir():
        raise ConfigError(f"project path is not a directory: {project}")
    used: set[str] = set()
    sources: list[Source] = []
    warnings: list[str] = []

    for name in ("AGENTS.override.md", "AGENTS.md"):
        candidate = project / name
        if candidate.is_file():
            sources.append(
                Source(
                    id=_unique("agents-override" if "override" in name else "agents-root", used),
                    kind="agents_root",
                    path=name,
                    loads=LOADS["agents_root"],
                    documented_reinjected=DOCUMENTED_REINJECTED["agents_root"],
                    size_bytes=candidate.stat().st_size,
                )
            )
    if not sources:
        warnings.append("no AGENTS.md or AGENTS.override.md at the project root")

    total = 0
    for source in sources:
        total += source.size_bytes
        if total > CODEX_COMBINED_LIMIT_BYTES:
            warnings.append(
                f"{source.path}: the combined AGENTS size passes Codex's default "
                f"{CODEX_COMBINED_LIMIT_BYTES}-byte limit here, so it may not be loaded at all"
            )

    for nested in _walk(project, "AGENTS.md"):
        relative = nested.relative_to(project)
        if relative == Path("AGENTS.md"):
            continue
        sources.append(
            Source(
                id=_unique(f"agents-{_slug(str(relative.parent))}", used),
                kind="agents_nested",
                path=str(relative),
                loads=LOADS["agents_nested"],
                documented_reinjected=DOCUMENTED_REINJECTED["agents_nested"],
                size_bytes=nested.stat().st_size,
            )
        )
    return Discovery(agent="codex", project=str(project), sources=sources, warnings=warnings)


def discover(agent: str, project: Path) -> Discovery:
    name = agent.strip().lower().replace("_", "-")
    if name in {"claude-code", "claude"}:
        return discover_claude_code(project)
    if name == "codex":
        return discover_codex(project)
    raise ConfigError(f"unknown agent {agent!r}; expected claude-code or codex")
