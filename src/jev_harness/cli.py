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


def main() -> None:
    """Console entry point: translate harness errors into exit code 2."""
    try:
        app()
    except HarnessError as exc:
        sys.stderr.write(f"error: {exc}\n")
        raise SystemExit(EXIT_ERROR) from exc


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    main()
