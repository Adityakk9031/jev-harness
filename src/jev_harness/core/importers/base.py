"""Importer protocol, session references, and the registry.

This module is not in the brief's file list; it exists so the three importers
can share types without importing each other (putting them in
``importers/__init__.py`` would be a cycle). Every importer must obey two rules:

* **Never guess.** A file that yields no turns raises :class:`ImporterError`
  instead of returning an empty trace, because an empty trace scores as
  "nothing survived".
* **Report what it skipped.** Record types and block types that were not
  understood are counted in ``Trace.meta``, so an unrecognized format shows up
  in the report instead of silently reducing the totals.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import HarnessError
from jev_harness.core.trace import Trace


class ImporterError(HarnessError):
    """A session could not be discovered or parsed."""


class SessionRef(BaseModel):
    """A discovered session, without its contents."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    path: Path | None = None
    started_at: datetime | None = None
    cwd: str | None = None
    title: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)

    def __str__(self) -> str:
        when = self.started_at.date().isoformat() if self.started_at else "unknown-date"
        return f"{self.agent}:{self.id} ({when})"


class TraceImporter(Protocol):
    """What every importer provides."""

    name: str

    def available(self) -> bool:
        """Whether this importer can find anything in this environment."""

    def list_sessions(self, *, limit: int | None = None) -> list[SessionRef]:
        """Discovered sessions, newest first."""

    def load(self, session: SessionRef | str) -> Trace:
        """Parse one session into a :class:`Trace`."""


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def first_lines(path: Path, count: int) -> list[str]:
    """Read at most ``count`` non-empty lines, for cheap metadata scans."""
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            if raw.strip():
                lines.append(raw)
            if len(lines) >= count:
                break
    return lines


def get_importer(name: str, **kwargs: object) -> TraceImporter:
    """Build an importer by name, e.g. ``claude_code``, ``codex``, ``contextleleo``."""
    from jev_harness.core.importers.claude_code import ClaudeCodeImporter
    from jev_harness.core.importers.codex import CodexImporter
    from jev_harness.core.importers.contextleleo import ContextleleoImporter

    normalized = name.strip().lower().replace("-", "_")
    builders: dict[str, Callable[..., TraceImporter]] = {
        "claude_code": ClaudeCodeImporter,
        "claude": ClaudeCodeImporter,
        "codex": CodexImporter,
        "contextleleo": ContextleleoImporter,
    }
    builder = builders.get(normalized)
    if builder is None:
        known = ", ".join(sorted(builders))
        raise ImporterError(f"unknown importer {name!r}; known importers: {known}")
    return builder(**kwargs)
