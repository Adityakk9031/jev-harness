"""Measure a real agent: load sources, compact, probe, in a fresh session per measurement point.

Why a fresh session per point: a probe's answer repeats the codewords, and a conversation that
contains its own answers can "remember" them through the summary rather than through the
instruction file. So each point (trial, round) starts a new session, loads the sources, compacts
``round`` times, and is probed exactly once.

Compaction is confirmed, never assumed: a ``PreCompact`` hook installed in the disposable copy
appends to a marker file, and a point whose compaction left no mark is discarded rather than
counted as survival. That makes an unverified trigger prompt safe to try.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from jev_harness.core.errors import HarnessError
from jev_harness.core.runner import AgentRunner, Budget, RunResult
from jev_harness.core.workspace import Workspace
from jev_harness.modules.compaction_survival.canary import CanaryPosition, Plan, plant
from jev_harness.modules.compaction_survival.results import Observation, StrategyRun
from jev_harness.modules.compaction_survival.sources import Discovery

MARKER_FILE = ".jev-compactions"
DEFAULT_TRIGGER_PROMPT = "/compact"
LOAD_PROMPT = (
    "Read each of these files, then reply with the single word ready and nothing else: {paths}"
)
LOAD_PROMPT_NO_FILES = "Reply with the single word ready and nothing else."
RECALL_PROMPT = (
    "Without reading any files or using any tools, list every 'Codeword for <name>' line from "
    "your project instructions that you can still recall, one per line, in the form "
    "'<name>: <CODEWORD>'. If you do not recall one, leave it out."
)
BEHAVIOR_PROMPT = (
    "Create a file named {name} in the project root containing the single word hello, "
    "following your project instructions for new files. Reply done when finished."
)


class CompactionNotConfirmedError(HarnessError):
    """The trigger prompt did not make the agent compact, so no survival rate is meaningful."""


@dataclass(slots=True)
class RealRunners:
    """Runners per phase. The recall probe must run with tools disabled."""

    load: AgentRunner
    probe: AgentRunner
    action: AgentRunner


def planned_calls(rounds: int, trials: int, behavior_sources: int) -> int:
    """How many agent invocations a real run makes (for ``--dry-run`` and ``--max-runs``)."""
    per_trial = sum(1 + r + 1 + behavior_sources for r in range(rounds + 1))
    return trials * per_trial


def reply_text(result: RunResult) -> str:
    """The agent's final reply, from the JSON envelope or the parsed trace."""
    if result.raw is not None:
        value = result.raw.get("result")
        if isinstance(value, str):
            return value
    if result.trace is not None:
        for turn in reversed(result.trace.turns):
            if turn.role == "assistant" and turn.content:
                return turn.content
    return ""


def _install_marker_hook(workspace: Workspace) -> None:
    relative = ".claude/settings.local.json"
    existing: dict[str, object] = {}
    if workspace.exists(relative):
        try:
            loaded = json.loads(workspace.read_file(relative))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}
    hooks = existing.get("hooks")
    hooks = dict(hooks) if isinstance(hooks, dict) else {}
    pre = list(hooks.get("PreCompact", [])) if isinstance(hooks.get("PreCompact"), list) else []
    pre.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": f'echo compacted >> "$CLAUDE_PROJECT_DIR/{MARKER_FILE}"',
                }
            ]
        }
    )
    hooks["PreCompact"] = pre
    existing["hooks"] = hooks
    workspace.plant_file(relative, json.dumps(existing, indent=2))


def _compactions(workspace: Workspace) -> int:
    if not workspace.exists(MARKER_FILE):
        return 0
    return len([line for line in workspace.read_file(MARKER_FILE).splitlines() if line.strip()])


async def run_real(
    discovery: Discovery,
    plan: Plan,
    runners: RealRunners,
    *,
    rounds: int,
    trials: int,
    budget: Budget,
    trigger_prompt: str = DEFAULT_TRIGGER_PROMPT,
    position: CanaryPosition = "end",
    progress: Callable[[str], None] | None = None,
) -> StrategyRun:
    run = StrategyRun(
        name="real-agent",
        mode="real",
        deterministic=False,
        rounds=rounds,
        trials=trials,
        notes=[f"compaction trigger prompt: {trigger_prompt!r}"],
    )
    from pathlib import Path

    project = Path(discovery.project)
    triggers = [c.source.trigger_path for c in plan.canaries if c.source.trigger_path]
    load_prompt = (
        LOAD_PROMPT.format(paths=", ".join(triggers)) if triggers else LOAD_PROMPT_NO_FILES
    )
    confirmed_any = False

    for trial in range(trials):
        with Workspace.create(project, name=f"compaction-{trial}") as workspace:
            plant(workspace, plan, position=position)
            _install_marker_hook(workspace)
            for round_ in range(rounds + 1):
                if progress:
                    progress(f"trial {trial + 1}/{trials}, after {round_} compaction(s)")
                loaded = await runners.load.run(
                    load_prompt, workspace=workspace.path, budget=budget
                )
                if not loaded.ok():
                    run.invalid_points += 1
                    run.notes.append(f"trial {trial} round {round_}: the load step failed")
                    continue
                session = loaded.session_id
                valid = True
                for _ in range(round_):
                    before = _compactions(workspace)
                    compacted = await runners.load.run(
                        trigger_prompt, workspace=workspace.path, session=session, budget=budget
                    )
                    session = compacted.session_id or session
                    if _compactions(workspace) <= before:
                        valid = False
                        break
                    confirmed_any = True
                if not valid:
                    run.invalid_points += 1
                    if not confirmed_any and round_ >= 1:
                        raise CompactionNotConfirmedError(
                            f"the trigger prompt {trigger_prompt!r} did not make the agent compact "
                            "(no PreCompact hook fired), so no survival rate can be measured. "
                            "Try --trigger-prompt, or see docs/AGENT_CLIS.md for the candidates."
                        )
                    run.notes.append(
                        f"trial {trial} round {round_}: a compaction was not confirmed; "
                        "point discarded"
                    )
                    continue

                recall = await runners.probe.run(
                    RECALL_PROMPT, workspace=workspace.path, session=session, budget=budget
                )
                answer = reply_text(recall)
                behavior: dict[str, bool | None] = {}
                for canary in plan.behavior():
                    name = f"jev-probe-{canary.source.id}-t{trial}-r{round_}.txt"
                    await runners.action.run(
                        BEHAVIOR_PROMPT.format(name=name),
                        workspace=workspace.path,
                        session=session,
                        budget=budget,
                    )
                    if workspace.exists(name):
                        first = workspace.read_file(name).splitlines()[:1]
                        behavior[canary.source.id] = (
                            bool(first) and first[0].strip() == canary.header
                        )
                    else:
                        behavior[canary.source.id] = None
                        run.notes.append(f"{name}: the agent did not create the probe file")
                for canary in plan.canaries:
                    run.observations.append(
                        Observation(
                            source_id=canary.source.id,
                            round=round_,
                            trial=trial,
                            recalled=canary.codeword in answer,
                            behavior_followed=behavior.get(canary.source.id),
                        )
                    )
    return run
