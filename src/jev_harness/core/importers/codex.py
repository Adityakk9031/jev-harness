"""Codex session importer.

Codex exposes two different artefacts and they are not the same format:

* **The documented event stream** - ``codex exec --json`` writes newline-delimited
  events: ``thread.started``, ``turn.started``, ``item.started``, ``item.completed``,
  ``turn.completed`` (with ``usage``), ``turn.failed``, ``error``. Item types
  include ``agent_message``, ``command_execution``, and others such as file
  changes, MCP calls, web searches and plan updates. This is what :meth:`parse_text`
  parses, which is why :class:`~jev_harness.core.runner.CodexRunner` can reuse it.
* **The on-disk rollout file** - ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl``.
  Its line format is visible in Codex's own test fixtures but is not documented
  as a stable interface, so this importer does not guess at it: unrecognized
  records are counted in ``Trace.meta["skipped_record_types"]`` and a file that
  yields no turns raises :class:`ImporterError` naming what it skipped. A real
  rollout sample is still required before claiming rollout support
  (see ``docs/AGENT_CLIS.md``).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jev_harness.core.importers.base import ImporterError, SessionRef, first_lines
from jev_harness.core.trace import Trace, Turn, TurnRole

#: Token accounting keys summed into ``Trace.meta["usage"]``.
USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)

#: Item types that are tool executions, mapped to the tool name we record.
TOOL_ITEMS = {
    "command_execution": "shell",
    "file_change": "file_change",
    "mcp_call": "mcp",
    "web_search": "web_search",
}

METADATA_SCAN_LINES = 20

#: On-disk rollout files are named ``rollout-<timestamp>-<session-id>``; the
#: trailing UUID is the useful part of the name. This is a naming convenience
#: only - the line format of those files is not supported (see the module docstring).
_ROLLOUT_ID = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


class CodexImporter:
    """Reads Codex ``exec --json`` event streams and session files."""

    name = "codex"

    def __init__(self, root: Path | None = None, *, executable: str = "codex") -> None:
        self.root = root if root is not None else _default_root()
        self.executable = executable

    # ------------------------------------------------------------ discovery

    def available(self) -> bool:
        return self.root.is_dir() and any(self.root.glob("**/*.jsonl"))

    def list_sessions(self, *, limit: int | None = None) -> list[SessionRef]:
        if not self.root.is_dir():
            return []

        refs = [
            SessionRef(
                id=_session_id_from_name(path),
                agent=self.name,
                path=path,
                # The documented event stream carries no timestamps, so the
                # file's modification time is the only honest answer.
                started_at=datetime.fromtimestamp(path.stat().st_mtime).astimezone(),
                cwd=_first_cwd(path),
                size_bytes=path.stat().st_size,
            )
            for path in sorted(self.root.glob("**/*.jsonl"))
        ]
        refs.sort(key=lambda ref: (ref.started_at is not None, ref.started_at), reverse=True)
        return refs[:limit] if limit is not None else refs

    # -------------------------------------------------------------- parsing

    def load(self, session: SessionRef | str) -> Trace:
        path = self._resolve(session)
        return self.load_path(path)

    def _resolve(self, session: SessionRef | str) -> Path:
        if isinstance(session, SessionRef):
            if session.path is None:
                raise ImporterError(f"session {session.id} has no path to read")
            return session.path
        candidate = Path(session).expanduser()
        if candidate.is_file():
            return candidate
        for ref in self.list_sessions():
            if ref.id == session or (ref.path is not None and ref.path.stem == session):
                if ref.path is None:  # pragma: no cover - list_sessions always sets a path
                    raise ImporterError(f"session {session} has no path")
                return ref.path
        raise ImporterError(f"no Codex session {session!r} under {self.root}")

    def load_path(self, path: Path) -> Trace:
        if not path.is_file():
            raise ImporterError(f"session file not found: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        return self.parse_text(text, source=str(path))

    def parse_text(self, text: str, *, source: str) -> Trace:
        """Parse an event stream that is already in memory (a file or stdout)."""
        turns: list[Turn] = []
        skipped: Counter[str] = Counter()
        skipped_items: Counter[str] = Counter()
        usage: Counter[str] = Counter()
        errors: list[str] = []
        thread_id: str | None = None
        session_id = Path(source).stem or "codex-session"
        lines_read = 0
        unparseable = 0
        turn_count = 0

        for raw in text.splitlines():
            if not raw.strip():
                continue
            lines_read += 1
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                unparseable += 1
                continue
            if not isinstance(record, dict):
                unparseable += 1
                continue

            event = record.get("type")
            if event == "thread.started" and isinstance(record.get("thread_id"), str):
                thread_id = record["thread_id"]
                session_id = thread_id
            elif event == "turn.started":
                turn_count += 1
            elif event == "turn.completed":
                record_usage = record.get("usage")
                if isinstance(record_usage, dict):
                    for key in USAGE_KEYS:
                        value = record_usage.get(key)
                        if isinstance(value, int):
                            usage[key] += value
            elif event in ("turn.failed", "error"):
                errors.append(_describe_error(record))
            elif event in ("item.started", "item.completed"):
                item = record.get("item")
                if not isinstance(item, dict):
                    skipped_items["<item-missing>"] += 1
                    continue
                turns.extend(_item_turns(event, item, skipped_items, errors))
            else:
                skipped[str(event)] += 1

        if not turns:
            raise ImporterError(
                f"{source} produced no turns ({lines_read} records read, {unparseable} "
                f"unparseable, skipped record types: {dict(sorted(skipped.items()))}, "
                f"skipped item types: {dict(sorted(skipped_items.items()))}). "
                "If this is an on-disk rollout file, its line format is not the documented "
                "`codex exec --json` event stream and is not supported yet."
            )

        renumbered = [
            turn.model_copy(update={"index": position}) for position, turn in enumerate(turns)
        ]
        return Trace(
            agent=self.name,
            session_id=session_id,
            turns=renumbered,
            meta={
                "source_path": source,
                "thread_id": thread_id,
                "lines_read": lines_read,
                "unparseable_records": unparseable,
                "turns_started": turn_count,
                "skipped_record_types": dict(sorted(skipped.items())),
                "skipped_item_types": dict(sorted(skipped_items.items())),
                "usage": {key: usage[key] for key in USAGE_KEYS if usage[key]},
                "errors": errors[:10],
            },
        )


def _default_root() -> Path:
    import os

    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home) if codex_home else Path.home() / ".codex"
    return base / "sessions"


def _session_id_from_name(path: Path) -> str:
    match = _ROLLOUT_ID.search(path.stem)
    return match.group(1) if match else path.stem


def _first_cwd(path: Path) -> str | None:
    for raw in first_lines(path, METADATA_SCAN_LINES):
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            cwd = record.get("cwd")
            if isinstance(cwd, str):
                return cwd
    return None


def _describe_error(record: dict[str, Any]) -> str:
    for key in ("message", "error"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(record, sort_keys=True, default=str)


def _item_turns(
    event: str,
    item: dict[str, Any],
    skipped_items: Counter[str],
    errors: list[str],
) -> list[Turn]:
    item_type = item.get("type")
    item_id = item.get("id") if isinstance(item.get("id"), str) else None

    if item_type == "agent_message":
        if event != "item.completed":
            return []
        return [Turn(role=TurnRole.assistant, content=_as_text(item.get("text")))]

    if item_type == "reasoning":
        if event != "item.completed":
            return []
        return [
            Turn(
                role=TurnRole.assistant,
                reasoning=_as_text(item.get("text")),
                token_count=_as_int(item.get("token_count")),
            )
        ]

    if item_type in TOOL_ITEMS:
        tool_name = TOOL_ITEMS[item_type]
        if event == "item.started":
            return [
                Turn(
                    role=TurnRole.tool_call,
                    tool_name=tool_name,
                    tool_args=_tool_args(item_type, item),
                    tool_id=item_id,
                )
            ]
        return [
            Turn(
                role=TurnRole.tool_result,
                content=_as_text(item.get("aggregated_output") or item.get("output") or ""),
                tool_id=item_id,
            )
        ]

    if item_type in ("plan_update", "todo_list"):
        if event != "item.completed":
            return []
        return [Turn(role=TurnRole.system, content=_as_text(item.get("text") or item))]

    if item_type == "error":
        errors.append(_as_text(item.get("message") or item))
        return []

    skipped_items[str(item_type)] += 1
    return []


def _tool_args(item_type: str, item: dict[str, Any]) -> dict[str, Any] | None:
    if item_type == "command_execution":
        command = item.get("command")
        return {"command": command} if command is not None else None
    for key in ("arguments", "args", "input", "changes"):
        value = item.get(key)
        if isinstance(value, dict):
            return value
        if value is not None:
            return {key: value}
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
