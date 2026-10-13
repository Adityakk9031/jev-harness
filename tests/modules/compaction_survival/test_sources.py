from __future__ import annotations

from pathlib import Path

import pytest

from jev_harness.core.errors import ConfigError
from jev_harness.modules.compaction_survival.sources import (
    CODEX_COMBINED_LIMIT_BYTES,
    discover,
    discover_claude_code,
    discover_codex,
    glob_to_probe_path,
    parse_frontmatter,
)


def by_id(project: Path) -> dict[str, object]:
    return {s.id: s for s in discover_claude_code(project).sources}


def test_every_claude_code_source_kind_is_found_with_how_it_loads(claude_project: Path) -> None:
    sources = {s.id: s for s in discover_claude_code(claude_project).sources}
    assert {s.kind for s in sources.values()} == {
        "root",
        "import",
        "rule",
        "scoped_rule",
        "nested",
    }
    assert sources["root"].path == "CLAUDE.md"
    assert sources["root"].documented_reinjected is True
    assert sources["import-extra"].path == "docs/extra.md"
    assert sources["rule-style"].documented_reinjected is True
    scoped = sources["scoped-py"]
    assert scoped.documented_reinjected is False
    assert scoped.globs == ["src/**/*.py"]
    assert scoped.trigger_path == "src/jev/probe.py"
    assert scoped.trigger_planted is True
    nested = sources["nested-sub"]
    assert nested.trigger_path == "sub/.jev-trigger.txt"
    assert "reloaded on demand" in nested.loads


def test_nothing_is_found_in_an_empty_project_and_the_reason_is_given(tmp_path: Path) -> None:
    result = discover_claude_code(tmp_path)
    assert result.sources == []
    assert any("no CLAUDE.md" in w for w in result.warnings)


def test_root_file_in_dot_claude_is_accepted(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("rules")
    sources = discover_claude_code(tmp_path).sources
    assert [(s.id, s.path) for s in sources] == [("root", ".claude/CLAUDE.md")]


def test_import_chain_follows_hops_ignores_cycles_and_outside_files(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("see @a.md and @../outside.md and @missing.md\n")
    (tmp_path / "a.md").write_text("@b.md\n")
    (tmp_path / "b.md").write_text("@CLAUDE.md @c.md\n")
    (tmp_path / "c.md").write_text("@d.md\n")
    (tmp_path / "d.md").write_text("@e.md\n")
    (tmp_path / "e.md").write_text("too deep\n")
    ids = [s.id for s in discover_claude_code(tmp_path).sources]
    assert ids == ["root", "import-a", "import-b", "import-c", "import-d"]  # e is 5 hops away


def test_email_addresses_and_code_spans_are_not_imports(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("mail me@example.com or use `@scope/pkg.js` syntax\n")
    assert [s.id for s in discover_claude_code(tmp_path).sources] == ["root"]


def test_local_file_and_skipped_directories(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("root")
    (tmp_path / "CLAUDE.local.md").write_text("mine")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "CLAUDE.md").write_text("vendored")
    sources = discover_claude_code(tmp_path).sources
    assert [(s.id, s.kind) for s in sources] == [("root", "root"), ("local", "local")]
    assert sources[1].documented_reinjected is None  # the docs are silent, so we do not assume


def test_skills_and_subagents_are_reported_as_not_probed(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("root")
    (tmp_path / ".claude" / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "deploy" / "SKILL.md").write_text("skill")
    (tmp_path / ".claude" / "agents").mkdir()
    (tmp_path / ".claude" / "agents" / "reviewer.md").write_text("agent")
    result = discover_claude_code(tmp_path)
    assert [s.id for s in result.sources] == ["root"]
    assert len(result.not_probed) == 2


def test_colliding_ids_are_made_unique(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("root")
    for directory in ("a-b", "a_b"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "CLAUDE.md").write_text("x")
    ids = [s.id for s in discover_claude_code(tmp_path).sources]
    assert len(ids) == len(set(ids)) == 3


def test_codex_chain_override_nested_and_size_limit(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("a" * 20_000)
    (tmp_path / "AGENTS.override.md").write_text("b" * 20_000)
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "AGENTS.md").write_text("nested")
    result = discover_codex(tmp_path)
    assert [(s.id, s.kind) for s in result.sources] == [
        ("agents-override", "agents_root"),
        ("agents-root", "agents_root"),
        ("agents-svc", "agents_nested"),
    ]
    assert any(str(CODEX_COMBINED_LIMIT_BYTES) in w for w in result.warnings)
    assert "working directory" in result.sources[2].loads


def test_discover_dispatches_and_rejects_unknown_agents(
    claude_project: Path, tmp_path: Path
) -> None:
    assert discover("claude", claude_project).agent == "claude-code"
    assert discover("codex", tmp_path).agent == "codex"
    with pytest.raises(ConfigError, match="unknown agent"):
        discover("cursor", tmp_path)
    with pytest.raises(ConfigError, match="not a directory"):
        discover_claude_code(tmp_path / "missing")


def test_frontmatter_and_glob_helpers() -> None:
    meta, body = parse_frontmatter("---\npaths: src/**\n---\nbody\n")
    assert meta == {"paths": "src/**"}
    assert body == "body\n"
    assert parse_frontmatter("no frontmatter") == ({}, "no frontmatter")
    with pytest.raises(ConfigError, match="frontmatter"):
        parse_frontmatter("---\na: [\n---\nbody")
    assert glob_to_probe_path("src/**/*.py") == "src/jev/probe.py"
    assert glob_to_probe_path("./**/*.{ts,tsx}") == "jev/probe.ts"
