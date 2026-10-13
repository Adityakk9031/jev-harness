"""Command line interface.

Exit codes are the contract (see :mod:`jev_harness.core.errors`): ``0`` clean,
``1`` the fail-on condition was met, ``2`` the harness could not run. Commands
raise :class:`HarnessError` rather than printing tracebacks, so CI logs stay
readable and a harness problem is never confused with a failing setup.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from jev_harness import __version__
from jev_harness.core.config import FailOn, Settings, load_settings, resolve_config_path
from jev_harness.core.errors import EXIT_ERROR, EXIT_FAIL, EXIT_OK, ConfigError, HarnessError
from jev_harness.core.importers import SessionRef, TraceImporter, get_importer
from jev_harness.core.judge import Judge
from jev_harness.core.judging import JudgeBudget
from jev_harness.core.report import (
    OutputFormat,
    Report,
    append_step_summary,
    workflow_annotations,
)

app = typer.Typer(
    add_completion=False,
    # Bare invocation is handled in the callback so that `--version` works
    # without a subcommand while a missing command still exits 2.
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode=None,
    help="Test your AI coding-agent setup the way you test your code.",
)
config_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Inspect the settings a command will actually use.",
)
app.add_typer(config_app, name="config")
sessions_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Discover, inspect, and export agent session transcripts.",
)
app.add_typer(sessions_app, name="sessions")
guard_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Do my hooks and permission rules block disguised dangerous commands?",
)
app.add_typer(guard_app, name="guard")
calibrate_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="When my gate says X% sure, how often is it right, and what threshold is safe?",
)
app.add_typer(calibrate_app, name="calibrate")
compaction_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Which of my agent's instructions survive context compaction?",
)
app.add_typer(compaction_app, name="compaction")
reviewers_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
    help="Which AI code reviewer catches real bugs in my code, and how noisy is it?",
)
app.add_typer(reviewers_app, name="reviewers")

#: The native importers ``sessions list`` covers by default. contextleleo is
#: opt-in because listing through it has no machine-readable interface.
NATIVE_AGENTS = ("claude-code", "codex")


@dataclass(slots=True)
class CliContext:
    """State shared by the root callback and the commands."""

    config_path: Path | None = None
    _settings: Settings | None = field(default=None, repr=False)

    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = load_settings(self.config_path)
        return self._settings


def _context(ctx: typer.Context) -> CliContext:
    if isinstance(ctx.obj, CliContext):
        return ctx.obj
    state = CliContext()
    ctx.obj = state
    return state


def _fail(message: str) -> NoReturn:
    """Report a harness problem and exit with code 2."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(EXIT_ERROR)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


@app.callback()
def root(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option("--config", metavar="PATH", help="Path to jev-harness.yml."),
    ] = None,
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the version and exit.", is_eager=True),
    ] = False,
) -> None:
    """Test your AI coding-agent setup the way you test your code."""
    if version:
        typer.echo(f"jev-harness {__version__}")
        raise typer.Exit(EXIT_OK)
    ctx.obj = CliContext(config_path=config)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(EXIT_ERROR)


@app.command("report")
def report_command(
    result: Annotated[
        Path,
        typer.Argument(metavar="RESULT", help="A module result JSON file."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", "-f", help="Rendering: md, json, or github."),
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None,
        typer.Option(
            "--fail-on",
            help=(
                "Exit non-zero when the result reaches this severity (flag or escalate). "
                "Off by default: rendering a stored result is not a judgement about it."
            ),
        ),
    ] = None,
    annotate: Annotated[
        bool,
        typer.Option("--annotations", help="Also print GitHub workflow commands."),
    ] = False,
) -> None:
    """Render a module result as Markdown, JSON, or a GitHub step summary."""
    try:
        target = OutputFormat.parse(output_format)
    except ValueError as exc:
        _fail(str(exc))

    payload = _read_json(result)
    try:
        report = Report.model_validate(payload)
    except ValueError as exc:
        _fail(f"{result} is not a jev-harness result: {exc}")

    text = report.render(target)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text, nl=False)

    if target is OutputFormat.github:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        append_step_summary(text, Path(summary) if summary else None)

    # Annotations are the machine-readable part of a CI run: GitHub reads them
    # from stdout, so `--format github` always emits them.
    if annotate or target is OutputFormat.github:
        for line in workflow_annotations(report):
            typer.echo(line)

    if fail_on is not None:
        try:
            threshold = FailOn(fail_on)
        except ValueError:
            _fail(f"unknown --fail-on value {fail_on!r}; expected flag or escalate")
        raise typer.Exit(EXIT_FAIL if report.should_fail(threshold) else EXIT_OK)
    raise typer.Exit(EXIT_OK)


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Print as JSON."),
    ] = False,
) -> None:
    """Show the resolved settings. Secret values are never printed."""
    state = _context(ctx)
    settings = state.settings()
    path = resolve_config_path(state.config_path)

    if as_json:
        document = {
            "config_file": str(path) if path is not None else None,
            "settings": settings.redacted(),
        }
        typer.echo(json.dumps(document, indent=2, sort_keys=True))
        raise typer.Exit(EXIT_OK)

    typer.echo(f"config_file: {path if path is not None else '(none found)'}")
    for key, value in sorted(settings.redacted().items()):
        typer.echo(f"{key}: {'(unset)' if value is None else value}")
    raise typer.Exit(EXIT_OK)


def _importer_for(agent: str, root: Path | None) -> TraceImporter:
    """Build the named importer, applying ``--root`` only where it means something."""
    name = agent.strip().lower().replace("-", "_")
    if root is not None:
        if name not in {"claude_code", "codex"}:
            raise ConfigError(
                f"--root applies to claude-code and codex session roots, not {agent!r}"
            )
        return get_importer(name, root=root)
    return get_importer(name)


def _agents_to_list(agent: str | None) -> list[str]:
    return list(NATIVE_AGENTS) if agent in (None, "all") else [agent]


def _one_line(value: object, limit: int = 60) -> str:
    flattened = " ".join(str(value).split())
    return flattened if len(flattened) <= limit else flattened[: limit - 3] + "..."


@sessions_app.command("list")
def sessions_list(
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            "-a",
            metavar="AGENT",
            help=(
                "claude-code, codex, or contextleleo. Default: both native "
                "importers (contextleleo listing has no machine-readable form)."
            ),
        ),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", metavar="N", help="Show at most N sessions per agent."),
    ] = 20,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            metavar="DIR",
            help=(
                "Override the session root for the selected --agent "
                "(~/.claude/projects or $CODEX_HOME/sessions). Requires --agent."
            ),
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
) -> None:
    """List discovered sessions, newest first."""
    if limit < 1:
        _fail("--limit must be at least 1")
    if root is not None and agent is None:
        _fail("--root needs --agent, so it is clear which session root is meant")

    agents = _agents_to_list(agent)
    refs: list[SessionRef] = []
    try:
        for name in agents:
            refs.extend(_importer_for(name, root).list_sessions(limit=limit))
    except HarnessError as exc:
        _fail(str(exc))

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "agents": agents,
                    "sessions": [ref.model_dump(mode="json") for ref in refs],
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise typer.Exit(EXIT_OK)

    if not refs:
        if root is None:
            typer.echo("no sessions found")
            if "claude-code" in agents:
                typer.echo("hint: Claude Code transcripts live under ~/.claude/projects/<slug>/")
            if "codex" in agents:
                typer.echo("hint: Codex rollout files live under $CODEX_HOME/sessions/YYYY/MM/DD/")
        else:
            typer.echo(f"no sessions under {root}")
        raise typer.Exit(EXIT_OK)

    for ref in refs:
        parts = [str(ref)]
        if ref.path is not None:
            parts.append(str(ref.path))
        if ref.title:
            parts.append(ref.title)
        typer.echo("  ".join(parts))
    raise typer.Exit(EXIT_OK)


@sessions_app.command("show")
def sessions_show(
    session: Annotated[
        str,
        typer.Argument(metavar="SESSION", help="A session id, or a path to a transcript file."),
    ],
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", metavar="AGENT", help="claude-code, codex, or contextleleo."),
    ],
    root: Annotated[
        Path | None,
        typer.Option("--root", metavar="DIR", help="Override the session root."),
    ] = None,
    turns: Annotated[
        int,
        typer.Option("--turns", metavar="N", help="Preview N turns (0 for a summary only)."),
    ] = 20,
    as_json: Annotated[bool, typer.Option("--json", help="Print the full trace as JSON.")] = False,
) -> None:
    """Summarize one session, with a preview of its first turns."""
    if turns < 0:
        _fail("--turns must not be negative")
    try:
        trace = _importer_for(agent, root).load(session)
    except HarnessError as exc:
        _fail(str(exc))

    if as_json:
        typer.echo(trace.model_dump_json(indent=2))
        raise typer.Exit(EXIT_OK)

    tokens = (
        f"{trace.token_total()} (sum of reported per-turn counts)"
        if trace.has_token_counts()
        else "not reported"
    )
    typer.echo(f"agent:     {trace.agent}")
    typer.echo(f"session:   {trace.session_id}")
    typer.echo(f"version:   {trace.agent_version or 'unknown'}")
    typer.echo(f"turns:     {len(trace.turns)} ({len(trace.tool_calls())} tool calls)")
    typer.echo(f"tokens:    {tokens}")
    if trace.meta:
        rendered = ", ".join(
            f"{key}={_one_line(value)}" for key, value in sorted(trace.meta.items())
        )
        typer.echo(f"meta:      {rendered}")

    if turns > 0 and trace.turns:
        shown = min(turns, len(trace.turns))
        typer.echo("")
        typer.echo(f"preview: first {shown} of {len(trace.turns)} turns")
        typer.echo(trace.slice(0, turns).to_text(max_chars_per_turn=300))
    raise typer.Exit(EXIT_OK)


@sessions_app.command("export")
def sessions_export(
    session: Annotated[
        str,
        typer.Argument(metavar="SESSION", help="A session id, or a path to a transcript file."),
    ],
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", metavar="AGENT", help="claude-code, codex, or contextleleo."),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    root: Annotated[
        Path | None,
        typer.Option("--root", metavar="DIR", help="Override the session root."),
    ] = None,
) -> None:
    """Export one session as a portable trace JSON document."""
    try:
        trace = _importer_for(agent, root).load(session)
    except HarnessError as exc:
        _fail(str(exc))

    text = trace.model_dump_json(indent=2)
    if output is None:
        typer.echo(text)
        raise typer.Exit(EXIT_OK)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    typer.echo(f"wrote {output} ({len(text)} bytes)")
    raise typer.Exit(EXIT_OK)


def _emit_report(
    report: Report,
    *,
    output_format: str,
    output: Path | None,
    fail_on: str | None,
    settings: Settings,
) -> NoReturn:
    """Render a module report, write it where asked, and exit with the right code."""
    try:
        target = OutputFormat.parse(output_format)
    except ValueError as exc:
        _fail(str(exc))
    text = report.render(target)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        typer.echo(f"wrote {output}")
    else:
        typer.echo(text, nl=False)
    if target is OutputFormat.github:
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        append_step_summary(text, Path(summary) if summary else None)
        for line in workflow_annotations(report):
            typer.echo(line)
    try:
        threshold = FailOn(fail_on) if fail_on is not None else settings.fail_on
    except ValueError:
        _fail(f"unknown --fail-on value {fail_on!r}; expected flag or escalate")
    raise typer.Exit(EXIT_FAIL if report.should_fail(threshold) else EXIT_OK)


@guard_app.command("run")
def guard_run(
    ctx: typer.Context,
    project: Annotated[
        Path,
        typer.Option("--project", metavar="PATH", help="Project whose guards are tested."),
    ] = Path(),
    corpus: Annotated[
        list[Path] | None,
        typer.Option("--corpus", metavar="FILE", help="Extra payload YAML (repeatable)."),
    ] = None,
    no_default_corpus: Annotated[
        bool, typer.Option("--no-default-corpus", help="Use only --corpus files.")
    ] = False,
    compare: Annotated[
        Path | None,
        typer.Option("--compare", metavar="FILE", help="A previous guard result (JSON)."),
    ] = None,
    guard: Annotated[
        list[str] | None,
        typer.Option(
            "--guard",
            metavar="COMMAND",
            help="An extra guard: any command that reads hook JSON on stdin (repeatable).",
        ),
    ] = None,
    no_user_settings: Annotated[
        bool, typer.Option("--no-user-settings", help="Ignore ~/.claude/settings.json.")
    ] = False,
    no_managed_settings: Annotated[
        bool, typer.Option("--no-managed-settings", help="Ignore managed settings.")
    ] = False,
    home: Annotated[
        Path | None,
        typer.Option("--home", metavar="DIR", help="Read user settings from DIR/.claude instead."),
    ] = None,
    hook_timeout: Annotated[
        float, typer.Option("--hook-timeout", metavar="SECONDS", help="Per-hook timeout.")
    ] = 10.0,
    pass_env: Annotated[
        list[str] | None,
        typer.Option("--pass-env", metavar="NAME", help="Environment variable hooks may see."),
    ] = None,
    isolate_home: Annotated[
        bool,
        typer.Option(
            "--isolate-home",
            help="Run hooks with a temporary HOME (user-level hooks will then not be found).",
        ),
    ] = False,
    judge: Annotated[str | None, typer.Option("--judge", help=JUDGE_HELP)] = None,
    judge_sample: Annotated[
        int,
        typer.Option(
            "--judge-sample",
            metavar="N",
            help="Most payloads to ask the judge about (spread evenly over disguise classes).",
        ),
    ] = 100,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", metavar="N", help=MAX_RUNS_JUDGE_HELP)
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Rendering: md, json, or github.")
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None,
        typer.Option("--fail-on", help="Exit 1 at this severity: flag or escalate."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would run; execute nothing."),
    ] = False,
) -> None:
    """Send disguised dangerous payloads to your guards and report what got through."""
    if hook_timeout <= 0:
        _fail("--hook-timeout must be positive")
    if judge_sample < 1:
        _fail("--judge-sample must be at least 1")
    try:
        _guard_run(
            _context(ctx).settings(),
            judge_name=judge,
            judge_sample=judge_sample,
            max_runs=max_runs,
            project=project,
            corpus=corpus or [],
            no_default_corpus=no_default_corpus,
            compare=compare,
            guard=guard or [],
            no_user_settings=no_user_settings,
            no_managed_settings=no_managed_settings,
            home=home,
            hook_timeout=hook_timeout,
            pass_env=pass_env or [],
            isolate_home=isolate_home,
            output_format=output_format,
            output=output,
            fail_on=fail_on,
            dry_run=dry_run,
        )
    except HarnessError as exc:
        _fail(str(exc))


def _guard_run(
    settings: Settings,
    *,
    judge_name: str | None,
    judge_sample: int,
    max_runs: int | None,
    project: Path,
    corpus: list[Path],
    no_default_corpus: bool,
    compare: Path | None,
    guard: list[str],
    no_user_settings: bool,
    no_managed_settings: bool,
    home: Path | None,
    hook_timeout: float,
    pass_env: list[str],
    isolate_home: bool,
    output_format: str,
    output: Path | None,
    fail_on: str | None,
    dry_run: bool,
) -> NoReturn:
    import asyncio

    from jev_harness.modules import guard_bench

    baseline: Report | None = None
    if compare is not None:
        try:
            baseline = Report.model_validate(_read_json(compare))
        except ValueError as exc:
            _fail(f"{compare} is not a jev-harness result: {exc}")

    options = guard_bench.GuardOptions(
        project=project,
        corpus_files=corpus,
        include_default_corpus=not no_default_corpus,
        generic_guards=guard,
        home=home if home is not None else (None if no_user_settings else Path.home()),
        include_managed=not no_managed_settings,
        hook_timeout=hook_timeout,
        pass_env=pass_env,
        isolate_home=isolate_home,
        baseline=baseline,
        min_samples=settings.min_samples,
        judge_sample=judge_sample,
        min_judge_confidence=settings.min_judge_confidence,
    )
    the_judge = _build_judge(settings, judge_name) if not dry_run else None
    if dry_run:
        plan = guard_bench.plan(options)
        for line in plan.lines():
            typer.echo(line)
        chosen = judge_name if judge_name is not None else settings.judge.value
        if chosen != "none":
            budget = _judge_budget(settings, max_runs)
            typer.echo(
                f"judge: {chosen}; up to {min(judge_sample, plan.payload_total)} calls "
                f"(cap {budget.max_calls}); asked only about payloads that get through"
            )
        raise typer.Exit(EXIT_OK)
    if the_judge is not None:
        options.judge = the_judge
        options.judge_budget = _judge_budget(settings, max_runs)

    report = asyncio.run(guard_bench.execute(options))
    _emit_report(
        report, output_format=output_format, output=output, fail_on=fail_on, settings=settings
    )


def _require_no_judge(judge: str, module: str) -> None:
    if judge != "none":
        _fail(f"{module} runs without a model; --judge must be 'none'")


JUDGE_HELP = "Model judge: none, jev, anthropic, or openai (default: JEV_JUDGE, else none)."
MAX_RUNS_JUDGE_HELP = "Cap on judge calls for this command (default 200)."


def _build_judge(settings: Settings, name: str | None) -> Judge | None:
    """The judge named on the command line (or by ``JEV_JUDGE``); ``None`` means rules only."""
    from jev_harness.core.config import JudgeBackend
    from jev_harness.core.judge import get_judge

    chosen = name if name is not None else settings.judge.value
    try:
        backend = JudgeBackend(chosen)
    except ValueError:
        _fail(f"unknown --judge {chosen!r}; expected none, jev, anthropic, or openai")
    if backend is JudgeBackend.none:
        return None
    return get_judge(settings.model_copy(update={"judge": backend}))


def _judge_budget(settings: Settings, max_runs: int | None) -> JudgeBudget:
    from jev_harness.core.judging import DEFAULT_MAX_JUDGE_CALLS

    cap = max_runs if max_runs is not None else settings.max_runs
    return JudgeBudget(max_calls=cap if cap is not None else DEFAULT_MAX_JUDGE_CALLS)


@calibrate_app.command("sheet")
def calibrate_sheet(
    log: Annotated[Path, typer.Argument(metavar="LOG", help="Gate log (JSONL or CSV).")],
    out: Annotated[
        Path, typer.Option("--out", "-o", metavar="PATH", help="Labelling sheet CSV to write.")
    ],
    n: Annotated[int, typer.Option("--n", metavar="N", help="Rows to sample.")] = 100,
    bins: Annotated[int, typer.Option("--bins", help="Confidence strata per decision.")] = 5,
    seed: Annotated[int, typer.Option("--seed", help="Sampling seed (reproducible).")] = 0,
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
) -> None:
    """Sample decisions, stratified by decision and confidence, into a CSV for a human to label."""
    from jev_harness.modules import gate_calibration

    _require_no_judge(judge, "calibrate")
    try:
        count = gate_calibration.make_sheet(log, out, n, bins=bins, seed=seed)
    except HarnessError as exc:
        _fail(str(exc))
    typer.echo(f"wrote {out} ({count} rows). Fill the `label` column with the correct decision.")
    raise typer.Exit(EXIT_OK)


@calibrate_app.command("report")
def calibrate_report(
    ctx: typer.Context,
    log: Annotated[Path, typer.Argument(metavar="LOG", help="Gate log (JSONL or CSV).")],
    labels: Annotated[
        Path | None,
        typer.Option("--labels", metavar="FILE", help="Filled sheet or id,label file."),
    ] = None,
    skip_decision: Annotated[
        str,
        typer.Option("--skip-decision", metavar="VALUE", help="The decision that skips the step."),
    ] = "skip",
    target: Annotated[
        float, typer.Option("--target", help="Miss-rate target for the upper bound.")
    ] = 0.01,
    ece_target: Annotated[
        float, typer.Option("--ece-target", help="Calibration error that triggers a finding.")
    ] = 0.05,
    threshold: Annotated[
        float | None,
        typer.Option("--threshold", help="Your current threshold, to evaluate explicitly."),
    ] = None,
    bins: Annotated[int, typer.Option("--bins", help="Reliability bins.")] = 10,
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Rendering: md, json, or github.")
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None, typer.Option("--fail-on", help="Exit 1 at this severity: flag or escalate.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate inputs and show counts; score nothing.")
    ] = False,
) -> None:
    """Reliability, calibration error, and a threshold with an upper-bound miss rate."""
    from jev_harness.modules import gate_calibration

    _require_no_judge(judge, "calibrate")
    settings = _context(ctx).settings()
    try:
        loaded = gate_calibration.load(log, labels)
        if dry_run:
            for line in gate_calibration.summarize_inputs(loaded, skip_decision):
                typer.echo(line)
            raise typer.Exit(EXIT_OK)
        report = gate_calibration.build_report(
            loaded,
            skip_decision=skip_decision,
            miss_target=target,
            ece_target=ece_target,
            bins=bins,
            min_samples=settings.min_samples,
            threshold=threshold,
        )
    except HarnessError as exc:
        _fail(str(exc))
    _emit_report(
        report, output_format=output_format, output=output, fail_on=fail_on, settings=settings
    )


@calibrate_app.command("gate")
def calibrate_gate(
    ctx: typer.Context,
    items: Annotated[
        Path,
        typer.Argument(
            metavar="ITEMS", help="JSONL items: an id plus the fields to show the judge."
        ),
    ],
    question: Annotated[
        str, typer.Option("--question", help="What the judge is asked about each item.")
    ],
    option: Annotated[
        list[str],
        typer.Option("--option", metavar="NAME=CRITERION", help="A choice it may give (repeat)."),
    ],
    out: Annotated[
        Path, typer.Option("--out", "-o", metavar="PATH", help="Gate log (JSONL) to write.")
    ],
    judge: Annotated[str | None, typer.Option("--judge", help=JUDGE_HELP)] = None,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", metavar="N", help=MAX_RUNS_JUDGE_HELP)
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate the items and show the call count.")
    ] = False,
) -> None:
    """Ask a judge (for example Jev) one question per item and log its answer and confidence."""
    import asyncio

    from jev_harness.modules import gate_calibration as module

    settings = _context(ctx).settings()
    try:
        loaded = module.load_items(items)
        options = module.parse_options(option)
        budget = _judge_budget(settings, max_runs)
        if dry_run:
            typer.echo(f"items:     {len(loaded)}")
            typer.echo(f"options:   {', '.join(options)}")
            typer.echo(f"judge calls: {len(loaded)} (cap {budget.max_calls})")
            typer.echo("dry run: no judge was called and nothing was written.")
            raise typer.Exit(EXIT_OK)
        the_judge = _build_judge(settings, judge)
        if the_judge is None:
            _fail("calibrate gate needs a judge: pass --judge jev (or anthropic, openai)")
        run = asyncio.run(
            module.run_gate(loaded, the_judge, budget, question=question, options=options)
        )
        errors_path = module.write_run(run, out)
    except HarnessError as exc:
        _fail(str(exc))
    typer.echo(f"wrote {out}: {len(run.rows)} decision(s) from {len(loaded)} item(s)")
    if run.errors:
        typer.echo(
            f"warning: {len(run.errors)} item(s) got no answer from the judge; see {errors_path}",
            err=True,
        )
    if not run.rows:
        _fail("the judge answered none of the items; check the key and the endpoint")
    typer.echo(
        "next: label a sample with `calibrate sheet`, then score it with `calibrate report` "
        "--skip-decision <the option that means skip>"
    )
    raise typer.Exit(EXIT_OK)


@calibrate_app.command("drift")
def calibrate_drift(
    ctx: typer.Context,
    log: Annotated[Path, typer.Argument(metavar="LOG", help="Gate log (JSONL or CSV).")],
    labels: Annotated[
        Path | None, typer.Option("--labels", metavar="FILE", help="Labels for LOG.")
    ] = None,
    split: Annotated[
        str | None,
        typer.Option(
            "--split", metavar="TIMESTAMP", help="Split LOG into before/after (ISO 8601)."
        ),
    ] = None,
    against: Annotated[
        Path | None,
        typer.Option("--against", metavar="LOG2", help="Compare LOG with a second log instead."),
    ] = None,
    against_labels: Annotated[
        Path | None, typer.Option("--against-labels", metavar="FILE", help="Labels for LOG2.")
    ] = None,
    bins: Annotated[int, typer.Option("--bins", help="Confidence bins to compare.")] = 5,
    min_per_window: Annotated[
        int, typer.Option("--min-per-window", help="Labelled rows needed per bin and window.")
    ] = 10,
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Rendering: md, json, or github.")
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None, typer.Option("--fail-on", help="Exit 1 at this severity: flag or escalate.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate inputs and show window sizes only.")
    ] = False,
) -> None:
    """Compare per-confidence-bin accuracy between two time windows, Holm-corrected."""
    from datetime import datetime

    from jev_harness.modules import gate_calibration

    _require_no_judge(judge, "calibrate")
    settings = _context(ctx).settings()
    try:
        first = gate_calibration.load(log, labels)
        second = gate_calibration.load(against, against_labels) if against is not None else None
        when = None
        if split is not None:
            try:
                when = datetime.fromisoformat(split.replace("Z", "+00:00"))
            except ValueError:
                _fail(f"--split {split!r} is not an ISO 8601 timestamp")
        window_a, window_b, description = gate_calibration.split_windows(
            first.rows, when, second.rows if second is not None else None
        )
        if dry_run:
            typer.echo(
                f"window A: {len(window_a)} rows ({sum(r.labeled for r in window_a)} labelled)"
            )
            typer.echo(
                f"window B: {len(window_b)} rows ({sum(r.labeled for r in window_b)} labelled)"
            )
            typer.echo("dry run: nothing was tested.")
            raise typer.Exit(EXIT_OK)
        report = gate_calibration.build_drift_report(
            window_a,
            window_b,
            bins=bins,
            min_per_window=min_per_window,
            description=description,
        )
    except HarnessError as exc:
        _fail(str(exc))
    _emit_report(
        report, output_format=output_format, output=output, fail_on=fail_on, settings=settings
    )


@compaction_app.command("run")
def compaction_run(
    ctx: typer.Context,
    project: Annotated[
        Path,
        typer.Option("--project", metavar="PATH", help="Project whose instructions are tested."),
    ] = Path(),
    agent: Annotated[str, typer.Option("--agent", help="claude-code or codex.")] = "claude-code",
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="simulated (free, offline, measures a summarizer) or real (calls the agent).",
        ),
    ] = "simulated",
    rounds: Annotated[int, typer.Option("--rounds", help="Compaction rounds.")] = 3,
    trials: Annotated[int, typer.Option("--trials", help="Independent repeats.")] = 5,
    strategy: Annotated[
        list[str] | None,
        typer.Option(
            "--strategy",
            help="Simulated strategy (repeatable): keep-recent, extractive, "
            "claude-code-documented. Default: keep-recent and claude-code-documented.",
        ),
    ] = None,
    summarizer: Annotated[
        str | None,
        typer.Option(
            "--summarizer",
            metavar="MODULE:FUNC",
            help="Your own summarizer: a function from a list of strings to a summary string.",
        ),
    ] = None,
    seed: Annotated[int, typer.Option("--seed", help="Seed for codewords and filler.")] = 0,
    filler_turns: Annotated[
        int, typer.Option("--filler-turns", help="Synthetic conversation turns (simulated).")
    ] = 40,
    canary_position: Annotated[
        str, typer.Option("--canary-position", help="Where the codeword goes: start or end.")
    ] = "end",
    behavior_sources: Annotated[
        int, typer.Option("--behavior-sources", help="Sources that also get a behavior rule.")
    ] = 2,
    floor: Annotated[
        float, typer.Option("--floor", help="Recall rate below which a source counts as lost.")
    ] = 0.8,
    trigger_prompt: Annotated[
        str, typer.Option("--trigger-prompt", help="Prompt that should make the agent compact.")
    ] = "/compact",
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", metavar="USD", help="Stop spending above this.")
    ] = None,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", metavar="N", help="Stop after N agent calls.")
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Model for real mode.")] = None,
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Rendering: md, json, or github.")
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None, typer.Option("--fail-on", help="Exit 1 at this severity: flag or escalate.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the plan and the call count; run nothing.")
    ] = False,
) -> None:
    """Plant canaries in a copy of your instructions, compact, and report what survived."""
    import asyncio

    from jev_harness.modules import compaction_survival as module
    from jev_harness.modules.compaction_survival.service import DEFAULT_STRATEGIES

    _require_no_judge(judge, "compaction")
    settings = _context(ctx).settings()
    options = module.CompactionOptions(
        project=project,
        agent=agent,
        mode=mode,
        rounds=rounds,
        trials=trials,
        strategies=strategy if strategy else ([] if summarizer else list(DEFAULT_STRATEGIES)),
        custom_summarizer=summarizer,
        seed=seed,
        filler_turns=filler_turns,
        position=canary_position,  # type: ignore[arg-type]
        behavior_sources=behavior_sources,
        floor=floor,
        trigger_prompt=trigger_prompt,
        max_cost_usd=max_cost if max_cost is not None else settings.max_cost_usd,
        max_runs=max_runs if max_runs is not None else settings.max_runs,
        model=model,
    )
    try:
        if dry_run:
            for line in module.plan_lines(options):
                typer.echo(line)
            raise typer.Exit(EXIT_OK)
        if mode == "real":
            report = asyncio.run(module.execute_real(options))
        else:
            report = module.execute_simulated(options)
    except HarnessError as exc:
        _fail(str(exc))
    _emit_report(
        report, output_format=output_format, output=output, fail_on=fail_on, settings=settings
    )


def _manifest_path(settings: Settings, manifest: Path | None) -> Path:
    return manifest if manifest is not None else settings.output_dir / "reviewers" / "manifest.json"


def _github_token(settings: Settings) -> str:
    from jev_harness.modules.reviewer_eval import resolve_token

    configured = settings.github_token.get_secret_value() if settings.github_token else None
    return resolve_token(configured)


@reviewers_app.command("mine")
def reviewers_mine(
    ctx: typer.Context,
    repo: Annotated[
        Path, typer.Option("--repo", metavar="PATH", help="Repository to mine.")
    ] = Path(),
    limit: Annotated[int, typer.Option("--limit", help="Recent commits to examine.")] = 200,
    max_lines: Annotated[
        int, typer.Option("--max-lines", help="Largest source change that counts as small.")
    ] = 40,
    since: Annotated[
        str | None, typer.Option("--since", metavar="DATE", help="Only commits after DATE.")
    ] = None,
    top: Annotated[int, typer.Option("--top", help="Candidates to keep.")] = 30,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", metavar="PATH", help="Candidates JSON.")
    ] = None,
    judge: Annotated[str | None, typer.Option("--judge", help=JUDGE_HELP)] = None,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", metavar="N", help=MAX_RUNS_JUDGE_HELP)
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the judge call count; call nothing.")
    ] = False,
) -> None:
    """List commits that look like small bug fixes with tests; a person confirms them."""
    import asyncio

    from jev_harness.modules import reviewer_eval as module

    settings = _context(ctx).settings()
    try:
        found = module.mine_candidates(repo, limit=limit, max_lines=max_lines, since=since)[:top]
        the_judge = _build_judge(settings, judge) if not dry_run else None
        if dry_run:
            chosen = judge if judge is not None else settings.judge.value
            typer.echo(f"candidates: {len(found)}")
            if chosen != "none":
                budget = _judge_budget(settings, max_runs)
                typer.echo(f"judge: {chosen}; {len(found)} calls (cap {budget.max_calls})")
            typer.echo("dry run: nothing was written and no judge was called.")
            raise typer.Exit(EXIT_OK)
        if the_judge is not None and found:
            found = asyncio.run(
                module.rank_candidates(
                    repo,
                    found,
                    the_judge,
                    _judge_budget(settings, max_runs),
                    min_confidence=settings.min_judge_confidence,
                )
            )
    except HarnessError as exc:
        _fail(str(exc))
    target = output if output is not None else settings.output_dir / "reviewers" / "candidates.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([c.model_dump() for c in found], indent=2) + "\n", encoding="utf-8"
    )
    if not found:
        typer.echo("no candidate bug fixes found (a candidate touches source and tests, is small,")
        typer.echo("and its message mentions a fix or links an issue)")
    for candidate in found:
        typer.echo(
            f"{candidate.sha[:8]}  score {candidate.score}  "
            f"{candidate.source_lines_changed:>3} lines  "
            + (f"[judge: {candidate.judge_label}]  " if candidate.judge_label else "")
            + candidate.subject[:70]
        )
    typer.echo(f'wrote {target}. Set "confirmed": true on the fixes you want, or pass --fix SHA.')
    raise typer.Exit(EXIT_OK)


@reviewers_app.command("build")
def reviewers_build(
    ctx: typer.Context,
    target_repo: Annotated[
        str, typer.Option("--target-repo", metavar="OWNER/NAME", help="The sandbox repository.")
    ],
    repo: Annotated[
        Path, typer.Option("--repo", metavar="PATH", help="Repository to read.")
    ] = Path(),
    fix: Annotated[
        list[str] | None, typer.Option("--fix", metavar="SHA", help="A confirmed fix (repeatable).")
    ] = None,
    candidates: Annotated[
        Path | None,
        typer.Option("--candidates", metavar="FILE", help="Use entries marked confirmed."),
    ] = None,
    controls: Annotated[
        int | None,
        typer.Option("--controls", help="Control PRs (default: one per fix, at most 3)."),
    ] = None,
    exclude: Annotated[
        list[str] | None,
        typer.Option("--exclude", metavar="GLOB", help="Remove matching paths from snapshots."),
    ] = None,
    git_url: Annotated[
        str | None,
        typer.Option("--git-url", metavar="URL", help="Remote URL (GitHub Enterprise or tests)."),
    ] = None,
    trigger_comment: Annotated[
        list[str] | None,
        typer.Option(
            "--trigger-comment",
            metavar="TEXT",
            help="Post this comment on every PR, e.g. '@coderabbitai review' (repeatable).",
        ),
    ] = None,
    request_reviewer: Annotated[
        list[str] | None,
        typer.Option(
            "--request-reviewer",
            metavar="LOGIN",
            help="Request a review from this login on every PR (repeatable).",
        ),
    ] = None,
    api_url: Annotated[
        str, typer.Option("--api-url", metavar="URL", help="GitHub API base URL.")
    ] = "https://api.github.com",
    manifest: Annotated[Path | None, typer.Option("--manifest", metavar="PATH")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm the push without a prompt.")] = False,
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be pushed; build and push nothing.")
    ] = False,
) -> None:
    """Re-introduce confirmed bugs as pull requests on a sandbox repository, plus control PRs."""
    import asyncio

    from jev_harness.modules import reviewer_eval as module

    _require_no_judge(judge, "reviewers")
    settings = _context(ctx).settings()
    try:
        fixes = list(fix or [])
        if candidates is not None:
            loaded = _read_json(candidates)
            if not isinstance(loaded, list):
                _fail(f"{candidates} must contain a list of candidates")
            marked = [
                str(c.get("sha")) for c in loaded if isinstance(c, dict) and c.get("confirmed")
            ]
            if not marked and not fixes:
                _fail(f'no entry in {candidates} has "confirmed": true; confirm the fixes you want')
            fixes.extend(marked)
        plan = module.plan_build(
            repo,
            target_repo,
            fixes,
            controls=controls,
            git_url=git_url,
            excludes=exclude or [],
            trigger_comments=trigger_comment or [],
            request_reviewers=request_reviewer or [],
        )
        if dry_run:
            for line in plan.confirmation_lines():
                typer.echo(line)
            typer.echo("")
            for pr in plan.prs:
                typer.echo(f"  {pr.kind:<8} {pr.id}  {pr.title!r}  files: {', '.join(pr.files)}")
            typer.echo("dry run: nothing was built, pushed, or opened.")
            raise typer.Exit(EXIT_OK)
        token = _github_token(settings)

        async def go() -> module.Manifest:
            async with module.GitHubClient(token, api_base=api_url) as client:
                return await module.execute_build(
                    plan,
                    client,
                    token if git_url is None or git_url.startswith("http") else None,
                    lambda lines: module.input_confirm(lines, assume_yes=yes),
                )

        built = asyncio.run(go())
    except module.ConfirmationDeclinedError as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    except HarnessError as exc:
        _fail(str(exc))
    path = _manifest_path(settings, manifest)
    module.save_manifest(built, path)
    for record in built.prs:
        typer.echo(f"{record.kind:<8} {record.id}  #{record.number}  {record.url}")
    for note in built.collection_notes:
        typer.echo(f"warning: {note}", err=True)
    typer.echo(f"wrote {path}. Wait for the reviewers, then run `reviewers collect`.")
    raise typer.Exit(EXIT_OK)


@reviewers_app.command("collect")
def reviewers_collect(
    ctx: typer.Context,
    manifest: Annotated[Path | None, typer.Option("--manifest", metavar="PATH")] = None,
    reviewer: Annotated[
        list[str] | None,
        typer.Option("--reviewer", metavar="LOGIN", help="Expected reviewer login (repeatable)."),
    ] = None,
    wait: Annotated[
        float, typer.Option("--wait", metavar="SECONDS", help="Poll for up to this long.")
    ] = 0.0,
    interval: Annotated[float, typer.Option("--interval", help="Seconds between polls.")] = 30.0,
    settle: Annotated[
        float, typer.Option("--settle", help="Stop after this long with no new comments.")
    ] = 120.0,
    local_reviewer: Annotated[
        list[str] | None,
        typer.Option(
            "--local-reviewer",
            metavar="NAME=COMMAND",
            help="A command that reviews a diff file; use {diff} for its path (repeatable).",
        ),
    ] = None,
    api_url: Annotated[
        str, typer.Option("--api-url", metavar="URL", help="GitHub API base URL.")
    ] = "https://api.github.com",
    judge: Annotated[str, typer.Option("--judge", help="Only 'none' is accepted.")] = "none",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be fetched; call nothing.")
    ] = False,
) -> None:
    """Fetch review comments from GitHub (and run local reviewer commands) into the manifest."""
    import asyncio

    from jev_harness.modules import reviewer_eval as module

    _require_no_judge(judge, "reviewers")
    settings = _context(ctx).settings()
    path = _manifest_path(settings, manifest)
    try:
        current = module.load_manifest(path)
        if dry_run:
            typer.echo(f"manifest:   {path}")
            typer.echo(f"repository: {current.target_repo}")
            typer.echo(f"pull requests: {sum(1 for p in current.prs if p.number)}")
            typer.echo(f"expected reviewers: {', '.join(reviewer or []) or '(any)'}")
            typer.echo(f"local reviewers: {len(local_reviewer or [])}")
            typer.echo("dry run: no request was made.")
            raise typer.Exit(EXIT_OK)
        token = _github_token(settings)

        async def go() -> module.Manifest:
            async with module.GitHubClient(token, api_base=api_url) as client:
                updated = await module.collect_github(
                    current,
                    client,
                    reviewers=list(reviewer or []),
                    wait_seconds=wait,
                    interval_seconds=interval,
                    settle_seconds=settle,
                )
                if local_reviewer:
                    updated = await module.run_local_reviewers(updated, client, local_reviewer)
                return updated

        updated = asyncio.run(go())
    except HarnessError as exc:
        _fail(str(exc))
    module.save_manifest(updated, path)
    typer.echo(
        f"collected {len(updated.comments)} comment(s) from {len(updated.reviewers())} reviewer(s)"
    )
    for note in updated.collection_notes[-3:]:
        typer.echo(f"note: {note}")
    typer.echo(
        "when you are done, run `reviewers cleanup` to close the PRs and delete the branches."
    )
    raise typer.Exit(EXIT_OK)


@reviewers_app.command("report")
def reviewers_report(
    ctx: typer.Context,
    manifest: Annotated[Path | None, typer.Option("--manifest", metavar="PATH")] = None,
    reviewer: Annotated[
        list[str] | None, typer.Option("--reviewer", metavar="LOGIN", help="Only these reviewers.")
    ] = None,
    slack: Annotated[
        int, typer.Option("--slack", help="Lines of tolerance around the re-introduced lines.")
    ] = 2,
    recall_floor: Annotated[float, typer.Option("--recall-floor")] = 0.5,
    noise_ceiling: Annotated[float, typer.Option("--noise-ceiling")] = 3.0,
    latency_ceiling: Annotated[float, typer.Option("--latency-ceiling", metavar="SECONDS")] = 900.0,
    min_prs: Annotated[int, typer.Option("--min-prs")] = 10,
    judge: Annotated[str | None, typer.Option("--judge", help=JUDGE_HELP)] = None,
    max_runs: Annotated[
        int | None, typer.Option("--max-runs", metavar="N", help=MAX_RUNS_JUDGE_HELP)
    ] = None,
    output_format: Annotated[
        str, typer.Option("--format", "-f", help="Rendering: md, json, or github.")
    ] = "md",
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", metavar="PATH", help="Write to a file instead of stdout."),
    ] = None,
    fail_on: Annotated[
        str | None, typer.Option("--fail-on", help="Exit 1 at this severity: flag or escalate.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the manifest's contents; score nothing.")
    ] = False,
) -> None:
    """Score the collected comments by location and render recall, precision, noise, and latency."""
    import asyncio

    from jev_harness.modules import reviewer_eval as module

    settings = _context(ctx).settings()
    path = _manifest_path(settings, manifest)
    try:
        current = module.load_manifest(path)
        chosen = judge if judge is not None else settings.judge.value
        if dry_run:
            typer.echo(f"manifest: {path} ({current.target_repo})")
            typer.echo(f"pull requests: {len(current.prs)}   comments: {len(current.comments)}")
            typer.echo(f"reviewers: {', '.join(current.reviewers()) or '(none yet)'}")
            if chosen != "none":
                budget = _judge_budget(settings, max_runs)
                typer.echo(
                    f"judge: {chosen}; {len(module.pending(current, slack))} comment(s) still to "
                    f"confirm (cap {budget.max_calls}); answers already stored are reused"
                )
            typer.echo("dry run: nothing was scored.")
            raise typer.Exit(EXIT_OK)
        the_judge = _build_judge(settings, judge)
        if the_judge is not None:
            made = asyncio.run(
                module.confirm_catches(
                    current, the_judge, _judge_budget(settings, max_runs), slack=slack
                )
            )
            if made:
                module.save_manifest(current, path)  # cache the answers so a rerun costs nothing
                typer.echo(f"judge: {made} call(s); answers saved in {path}", err=True)
        report = module.build_report(
            current,
            module.ReportOptions(
                slack=slack,
                reviewers=list(reviewer) if reviewer else None,
                recall_floor=recall_floor,
                noise_ceiling=noise_ceiling,
                latency_ceiling=latency_ceiling,
                min_prs=min_prs,
                min_judge_confidence=settings.min_judge_confidence,
            ),
        )
    except HarnessError as exc:
        _fail(str(exc))
    _emit_report(
        report, output_format=output_format, output=output, fail_on=fail_on, settings=settings
    )


@reviewers_app.command("cleanup")
def reviewers_cleanup(
    ctx: typer.Context,
    manifest: Annotated[Path | None, typer.Option("--manifest", metavar="PATH")] = None,
    keep: Annotated[
        bool, typer.Option("--keep", help="Leave the pull requests and branches in place.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
    api_url: Annotated[
        str, typer.Option("--api-url", metavar="URL", help="GitHub API base URL.")
    ] = "https://api.github.com",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="List what would be closed and deleted.")
    ] = False,
) -> None:
    """Close the PRs and delete the branches this run created, on the manifest's own repository."""
    import asyncio

    from jev_harness.modules import reviewer_eval as module

    settings = _context(ctx).settings()
    path = _manifest_path(settings, manifest)
    if keep:
        typer.echo("--keep: leaving pull requests and branches in place.")
        raise typer.Exit(EXIT_OK)
    try:
        current = module.load_manifest(path)
        open_prs = [p for p in current.prs if not p.closed]
        lines = [
            f"repository: {current.target_repo}",
            f"will close {sum(1 for p in open_prs if p.number)} pull request(s) and delete "
            f"{2 * len(open_prs)} branch(es):",
            *[f"  {p.head_branch}, {p.base_branch}" for p in open_prs],
        ]
        for line in lines:
            typer.echo(line)
        if dry_run or not open_prs:
            typer.echo("dry run: nothing was changed." if dry_run else "nothing left to clean up.")
            raise typer.Exit(EXIT_OK)
        if not yes and input("Type 'yes' to continue: ").strip() != "yes":
            typer.echo("not confirmed; nothing was changed.", err=True)
            raise typer.Exit(EXIT_ERROR)
        token = _github_token(settings)

        async def go() -> tuple[module.Manifest, list[str]]:
            async with module.GitHubClient(token, api_base=api_url) as client:
                return await module.cleanup(current, client)

        updated, actions = asyncio.run(go())
    except HarnessError as exc:
        _fail(str(exc))
    module.save_manifest(updated, path)
    for action in actions:
        typer.echo(action)
    raise typer.Exit(EXIT_OK)


def main() -> None:
    """Console entry point: translate harness errors into exit code 2."""
    try:
        app()
    except HarnessError as exc:
        sys.stderr.write(f"error: {exc}\n")
        raise SystemExit(EXIT_ERROR) from exc


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    main()
