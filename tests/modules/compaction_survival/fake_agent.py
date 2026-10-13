"""A scripted stand-in for ``claude -p`` that models what survives compaction.

It reads the instruction files from the workspace it is given, keeps the codewords it "loaded" in a
per-session context, loses them according to a policy when told to compact, and answers the recall
and behavior probes from what remains. It records every call so tests can assert the protocol:
a fresh session per measurement point, the recall probe run with tools disabled, and so on.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jev_harness.core.runner import Budget, RunResult, RunStatus
from jev_harness.modules.compaction_survival.real import (
    BEHAVIOR_PROMPT,
    DEFAULT_TRIGGER_PROMPT,
    MARKER_FILE,
    RECALL_PROMPT,
    SESSION_MARKER_FILE,
)

CODEWORD = re.compile(r"Codeword for (\S+): (\S+)")
HEADER = re.compile(r"first line must be exactly: (# jev-header-\S+)")

#: ``(source_path, compactions_so_far) -> still in context?``
Policy = Callable[[str, int], bool]


def keep_everything(path: str, compactions: int) -> bool:
    return True


@dataclass
class Session:
    #: path -> (id, codeword, header or None)
    context: dict[str, tuple[str, str, str | None]] = field(default_factory=dict)
    compactions: int = 0


@dataclass
class FakeAgent:
    name: str = "fake-claude"
    policy: Policy = keep_everything
    compact_works: bool = True
    hooks_run: bool = True
    follows_rules: bool = True
    tools_disabled: bool = False
    sessions: dict[str, Session] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    started: int = 0

    def available(self) -> bool:
        return True

    def version(self) -> str | None:
        return "fake-1.0"

    def plan(self, prompt: str, *, workspace: Path, session: str | None = None) -> list[str]:
        return ["fake", prompt]

    def _load(self, workspace: Path, prompt: str) -> Session:
        session = Session()
        wanted = prompt.split(":", 1)[1].split(",") if ":" in prompt else []
        triggers = {w.strip() for w in wanted}
        for path in sorted(workspace.rglob("*.md")):
            relative = str(path.relative_to(workspace))
            if relative.startswith(".claude/rules") and "paths:" in path.read_text():
                continue  # scoped rules load only when a matching file is read
            if "/" in relative and relative.endswith("CLAUDE.md") and not relative.startswith("."):
                trigger = str(Path(relative).parent / ".jev-trigger.txt")
                if trigger not in triggers:
                    continue
            self._ingest(session, relative, path.read_text())
        for trigger in triggers:
            if trigger.startswith("src/"):
                for rule in (workspace / ".claude" / "rules").glob("*.md"):
                    self._ingest(session, str(rule.relative_to(workspace)), rule.read_text())
        return session

    @staticmethod
    def _ingest(session: Session, relative: str, text: str) -> None:
        found = CODEWORD.search(text)
        if found:
            header = HEADER.search(text)
            session.context[relative] = (
                found.group(1),
                found.group(2),
                header.group(1) if header else None,
            )

    async def run(
        self,
        prompt: str,
        *,
        workspace: Path,
        session: str | None = None,
        budget: Budget | None = None,
    ) -> RunResult:
        if budget is not None:
            budget.check()
        self.calls.append(
            {"prompt": prompt, "session": session, "tools_disabled": self.tools_disabled}
        )
        text = "ok"
        if session is None:
            self.started += 1
            session = f"s{self.started}"
            self.sessions[session] = self._load(workspace, prompt)
            if self.hooks_run:
                with (workspace / SESSION_MARKER_FILE).open("a") as handle:
                    handle.write("started\n")
        else:
            state = self.sessions[session]
            if prompt == DEFAULT_TRIGGER_PROMPT:
                state.compactions += 1
                state.context = {
                    path: value
                    for path, value in state.context.items()
                    if self.policy(path, state.compactions)
                }
                if self.compact_works and self.hooks_run:
                    with (workspace / MARKER_FILE).open("a") as handle:
                        handle.write("compacted\n")
            elif prompt == RECALL_PROMPT:
                text = "\n".join(f"{i}: {code}" for i, code, _ in state.context.values())
            elif prompt.startswith(BEHAVIOR_PROMPT.split("{")[0]):
                name = re.search(r"named (\S+) in", prompt)
                assert name is not None
                # The agent follows the rule of the source named in the file, if still loaded.
                source_id = name.group(1).split("-t")[0].removeprefix("jev-probe-")
                matching = [h for i, _, h in state.context.values() if i == source_id and h]
                first = matching[0] if (matching and self.follows_rules) else "hello"
                (workspace / name.group(1)).write_text(f"{first}\nhello\n")
        result = RunResult(
            runner=self.name,
            status=RunStatus.ok,
            exit_status=0,
            session_id=session,
            cost_usd=0.01,
            raw={"result": text},
        )
        if budget is not None:
            budget.record(result)
        return result
