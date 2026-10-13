"""Importer protocol, registry and shared helper tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from jev_harness.core.importers import (
    ClaudeCodeImporter,
    CodexImporter,
    ContextleleoImporter,
    ImporterError,
    SessionRef,
    get_importer,
)
from jev_harness.core.importers.base import first_lines, parse_timestamp


def test_registry_resolves_every_importer_and_its_aliases() -> None:
    assert isinstance(get_importer("claude_code"), ClaudeCodeImporter)
    assert isinstance(get_importer("claude-code"), ClaudeCodeImporter)
    assert isinstance(get_importer("CLAUDE"), ClaudeCodeImporter)
    assert isinstance(get_importer("codex"), CodexImporter)
    assert isinstance(get_importer("contextleleo"), ContextleleoImporter)


def test_registry_rejects_unknown_names_and_lists_the_known_ones() -> None:
    with pytest.raises(ImporterError) as error:
        get_importer("cursor")
    assert "unknown importer 'cursor'" in str(error.value)
    assert "claude_code" in str(error.value)


def test_registry_passes_arguments_through() -> None:
    importer = get_importer("contextleleo", executable="ctx", harness="codex")
    assert isinstance(importer, ContextleleoImporter)
    assert importer.export_argv("x") == ["ctx", "export", "x", "--from", "codex"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-02T03:04:05.000Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        ("2026-01-02T03:04:05Z", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        # A naive timestamp is assumed UTC rather than treated as local time.
        ("2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)),
        (
            "2026-01-02T03:04:05+02:00",
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2))),
        ),
    ],
)
def test_timestamps_are_parsed_and_made_aware(value: str, expected: datetime) -> None:
    parsed = parse_timestamp(value)
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed == expected


@pytest.mark.parametrize("value", [None, "", "  ", "yesterday", 42, [], {}])
def test_unparseable_timestamps_return_none(value: object) -> None:
    assert parse_timestamp(value) is None


def test_session_ref_is_immutable_and_descriptive() -> None:
    ref = SessionRef(id="abc", agent="codex")
    assert str(ref) == "codex:abc (unknown-date)"
    with pytest.raises(ValidationError, match="frozen"):
        ref.id = "other"
    with pytest.raises(ValidationError, match="at least 1 character"):
        SessionRef(id="", agent="codex")


def test_first_lines_stops_early_and_skips_blanks(tmp_path: Path) -> None:
    path = tmp_path / "many.txt"
    path.write_text("a\n\nb\nc\nd\n")
    assert first_lines(path, 3) == ["a\n", "b\n", "c\n"]
