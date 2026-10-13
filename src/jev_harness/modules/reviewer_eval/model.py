"""Data that flows between the five ``reviewers`` commands, stored in one manifest file."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import ConfigError

MANIFEST_VERSION = 1
PRKind = Literal["bug", "control"]

Range = tuple[int, int]


class Candidate(BaseModel):
    """A commit that looks like a bug fix, offered for a human to confirm."""

    model_config = ConfigDict(extra="forbid")

    sha: str
    subject: str
    author_date: str
    score: int
    reasons: list[str]
    source_files: list[str]
    test_files: list[str]
    source_lines_changed: int
    confirmed: bool = False


class TargetHunk(BaseModel):
    """Where the re-introduced bug lives in the bug PR's diff.

    ``new_ranges`` are line ranges on the PR's head (RIGHT) side, ``old_ranges`` on its base
    (LEFT) side. A bug that removes a check is visible only on the left.
    """

    model_config = ConfigDict(extra="forbid")

    path: str
    new_ranges: list[Range] = Field(default_factory=list)
    old_ranges: list[Range] = Field(default_factory=list)


class PRRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: PRKind
    fix_sha: str
    base_branch: str
    head_branch: str
    title: str
    number: int | None = None
    url: str | None = None
    created_at: datetime | None = None
    targets: list[TargetHunk] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)
    closed: bool = False


class ReviewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str
    pr_id: str
    kind: Literal["inline", "summary"]
    body: str
    path: str | None = None
    line: int | None = None
    start_line: int | None = None
    side: Literal["LEFT", "RIGHT"] = "RIGHT"
    created_at: datetime | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = MANIFEST_VERSION
    target_repo: str
    git_url: str | None = None
    source_repo: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    prs: list[PRRecord] = Field(default_factory=list)
    comments: list[ReviewComment] = Field(default_factory=list)
    #: Reviewers that were expected to answer, so absence can be reported.
    expected_reviewers: list[str] = Field(default_factory=list)
    collection_notes: list[str] = Field(default_factory=list)

    def pr(self, pr_id: str) -> PRRecord:
        for record in self.prs:
            if record.id == pr_id:
                return record
        raise KeyError(pr_id)

    def reviewers(self) -> list[str]:
        names = {c.reviewer for c in self.comments} | set(self.expected_reviewers)
        return sorted(names)


def save_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    try:
        return Manifest.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"{path} is not a reviewers manifest: {exc}") from exc
