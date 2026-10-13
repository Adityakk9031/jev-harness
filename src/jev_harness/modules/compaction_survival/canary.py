"""Plant unique codewords and mechanical rules in a disposable copy of the project."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from jev_harness.core.workspace import Workspace
from jev_harness.modules.compaction_survival.sources import Discovery, Source

ADJECTIVES = (
    "amber", "brisk", "cobalt", "dusky", "ember", "fabled", "gilded", "hollow", "indigo", "jaded",
    "kindly", "lunar", "mossy", "nimble", "opal", "plush", "quiet", "russet", "sable", "tidal",
)  # fmt: skip
NOUNS = (
    "anvil", "beacon", "cipher", "dune", "easel", "falcon", "gantry", "harbor", "island", "juniper",
    "kettle", "lantern", "meadow", "nebula", "orchard", "pylon", "quarry", "ripple", "summit",
    "thistle",
)  # fmt: skip

CanaryPosition = Literal["start", "end"]


def make_codeword(rng: random.Random, taken: set[str]) -> str:
    """A pronounceable, effectively unguessable codeword such as ``AMBER-FALCON-4821``."""
    while True:
        word = f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}-{rng.randrange(1000, 10000)}".upper()
        if word not in taken:
            taken.add(word)
            return word


def codeword_line(source_id: str, codeword: str) -> str:
    return f"Codeword for {source_id}: {codeword}"


def rule_line(source_id: str, header: str) -> str:
    return f"When you create any new file, its first line must be exactly: {header}"


@dataclass(slots=True)
class Canary:
    source: Source
    codeword: str
    #: For behavior-probed sources: the exact first line a new file must start with.
    header: str | None = None


@dataclass(slots=True)
class Plan:
    """What was planted: the expected answers for scoring."""

    canaries: list[Canary] = field(default_factory=list)

    def by_id(self) -> dict[str, Canary]:
        return {c.source.id: c for c in self.canaries}

    def behavior(self) -> list[Canary]:
        return [c for c in self.canaries if c.header is not None]


def make_plan(
    discovery: Discovery,
    *,
    seed: int,
    behavior_sources: int = 2,
) -> Plan:
    rng = random.Random(seed)  # noqa: S311 - reproducible codewords, not security
    taken: set[str] = set()
    plan = Plan()
    behaviors_left = behavior_sources
    for source in discovery.sources:
        canary = Canary(source=source, codeword=make_codeword(rng, taken))
        if behaviors_left > 0:
            canary.header = f"# jev-header-{source.id}-{make_codeword(rng, taken).lower()}"
            behaviors_left -= 1
        plan.canaries.append(canary)
    return plan


def canary_text(canary: Canary) -> str:
    lines = [codeword_line(canary.source.id, canary.codeword)]
    if canary.header is not None:
        lines.append(rule_line(canary.source.id, canary.header))
    return "\n".join(lines)


def insert_block(original: str, block: str, position: CanaryPosition) -> str:
    """Add the canary block at the end of a file, or at the start after any frontmatter."""
    if position == "end":
        return original.rstrip("\n") + "\n\n" + block + "\n"
    match = re.match(r"^(---\s*\n.*?\n---\s*\n)(.*)$", original, re.DOTALL)
    head, body = (match.group(1), match.group(2)) if match else ("", original)
    return head + block + "\n\n" + body


def plant(
    workspace: Workspace,
    plan: Plan,
    *,
    position: CanaryPosition = "end",
) -> None:
    """Write canaries into the workspace copy. The real project is never touched."""
    for canary in plan.canaries:
        original = workspace.read_file(canary.source.path)
        workspace.plant_file(
            canary.source.path, insert_block(original, canary_text(canary), position)
        )
        _plant_trigger(workspace, canary.source)


def _plant_trigger(workspace: Workspace, source: Source) -> None:
    if source.trigger_path and source.trigger_planted and not workspace.exists(source.trigger_path):
        workspace.plant_file(source.trigger_path, "jev-harness trigger file\n")


def is_workspace_copy(workspace: Workspace, project: Path) -> bool:
    return workspace.path.resolve() != project.resolve()
