"""Claude Code session importer.

Layout: ``<root>/<project-slug>/<session-id>.jsonl``, where ``root`` defaults to
``~/.claude/projects`` and each line is one JSON record.

Shape observed on this machine (structure only, 2026-10-13) and used here:

* Conversation records carry ``type`` in ``user`` / ``assistant`` / ``system``
  with a ``message`` object holding ``role`` and ``content`` (a string, or a
  list of typed blocks).
* Records exist that are not conversation at all - ``queue-operation``,
  ``attachment``, ``last-prompt``, ``custom-title``, ``agent-name``,
  ``file-history-snapshot``, ``atis-latch``, ``cost-state`` - and are skipped
  and counted.
* Assistant records carry ``message.usage`` with token counts, ``isSidechain``
  marks subagent turns, and ``version`` identifies the client.

**Not observed locally:** no tool-call or tool-result records appeared in the
sample, so block handling for ``tool_use`` / ``tool_result`` follows the public
Anthropic content-block shape and is covered by a synthetic fixture rather than
by real data. See ``docs/AGENT_CLIS.md``.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from jev_harness.core.importers.base import (
    ImporterError,
    SessionRef,
    first_lines,
    parse_timestamp,
)
from jev_harness.core.trace import Trace, Turn, TurnRole

#: Conversational record types. Anything else is skipped and counted.
CONVERSATION_TYPES = frozenset({"user", "assistant", "system"})

#: Cap on how many lines are read for a metadata scan.
METADATA_SCAN_LINES = 40

#: Token accounting keys summed into ``Trace.meta["usage"]``.
USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class ClaudeCodeImporter:
    """Reads Claude Code session transcripts."""

    name = "claude_code"

    def __init__(self, root: Path | None = None, *, include_sidechain: bool = False) -> None:
        self.root = root if root is not None else Path.home() / ".claude" / "projects"
        self.include_sidechain = include_sidechain

    # ------------------------------------------------------------ discovery

    def available(self) -> bool:
        return self.root.is_dir() and any(self.root.glob("**/*.jsonl"))

    def list_sessions(self, *, limit: int | None = None) -> list[SessionRef]:
        if not self.root.is_dir():
            return []

        refs: list[SessionRef] = []
        for path in sorted(self.root.glob("**/*.jsonl")):
            try:
                refs.append(self._describe(path))
            except OSError:
                continue
        refs.sort(key=lambda ref: (ref.started_at is not None, ref.started_at), reverse=True)
        return refs[:limit] if limit is not None else refs

    def _describe(self, path: Path) -> SessionRef:
        started_at = None
        cwd = None
        title = None
        session_id = path.stem
        for raw in first_lines(path, METADATA_SCAN_LINES):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if started_at is None:
                started_at = parse_timestamp(record.get("timestamp"))
            if cwd is None and isinstance(record.get("cwd"), str):
                cwd = record["cwd"]
            if isinstance(record.get("sessionId"), str):
                session_id = record["sessionId"]
            if title is None and isinstance(record.get("customTitle"), str):
                title = record["customTitle"]
        return SessionRef(
            id=session_id,
            agent=self.name,
            path=path,
            started_at=started_at,
            cwd=cwd,
            title=title,
            size_bytes=path.stat().st_size,
        )

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
            if ref.id == session:
                if ref.path is None:  # pragma: no cover - list_sessions always sets a path
                    raise ImporterError(f"session {session} has no path")
                return ref.path
        raise ImporterError(
            f"no Claude Code session {session!r} under {self.root}; "
            "run `jev-harness sessions list --agent claude-code` to see what exists"
        )

    def load_path(self, path: Path) -> Trace:
        if not path.is_file():
            raise ImporterError(f"transcript not found: {path}")

        turns: list[Turn] = []
        skipped: Counter[str] = Counter()
        skipped_blocks: Counter[str] = Counter()
        usage: Counter[str] = Counter()
        sidechain_turns = 0
        unparseable = 0
        session_id = path.stem
        agent_version: str | None = None
        lines_read = 0

        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw in handle:
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

                if isinstance(record.get("sessionId"), str):
                    session_id = record["sessionId"]
                if agent_version is None and isinstance(record.get("version"), str):
                    agent_version = record["version"]

                record_type = record.get("type")
                if record_type not in CONVERSATION_TYPES:
                    skipped[str(record_type)] += 1
                    continue
                if record.get("isSidechain") is True and not self.include_sidechain:
                    sidechain_turns += 1
                    continue

                timestamp = parse_timestamp(record.get("timestamp"))
                message = record.get("message")
                if not isinstance(message, dict):
                    skipped[f"{record_type}:no-message"] += 1
                    continue

                role = _role_for(record_type, message.get("role"))
                record_usage = message.get("usage")
                token_count = None
                if isinstance(record_usage, dict):
                    for key in USAGE_KEYS:
                        value = record_usage.get(key)
                        if isinstance(value, int):
                            usage[key] += value
                    output_tokens = record_usage.get("output_tokens")
                    if isinstance(output_tokens, int):
                        token_count = output_tokens

                turns.extend(
                    _turns_from_content(
                        message.get("content"),
                        role=role,
                        timestamp=timestamp,
                        token_count=token_count,
                        skipped_blocks=skipped_blocks,
                    )
                )

        if not turns:
            raise ImporterError(
                f"{path} produced no turns ({lines_read} records read, "
                f"{unparseable} unparseable, skipped types: {dict(skipped)}). "
                "Refusing to report an empty transcript as a result."
            )

        renumbered = [
            turn.model_copy(update={"index": position}) for position, turn in enumerate(turns)
        ]
        return Trace(
            agent=self.name,
            agent_version=agent_version,
            session_id=session_id,
            turns=renumbered,
            meta={
                "source_path": str(path),
                "lines_read": lines_read,
                "unparseable_records": unparseable,
                "skipped_record_types": dict(sorted(skipped.items())),
                "skipped_block_types": dict(sorted(skipped_blocks.items())),
                "sidechain_records_skipped": sidechain_turns,
                "usage": {key: usage[key] for key in USAGE_KEYS if usage[key]},
                "tool_blocks_observed": any(t.role is TurnRole.tool_call for t in renumbered),
            },
        )


def _role_for(record_type: object, message_role: object) -> TurnRole:
    if record_type == "system":
        return TurnRole.system
    if message_role == "assistant" or record_type == "assistant":
        return TurnRole.assistant
    return TurnRole.user


def _turns_from_content(
    content: object,
    *,
    role: TurnRole,
    timestamp: datetime | None,
    token_count: int | None,
    skipped_blocks: Counter[str],
) -> list[Turn]:
    """Turn a ``message.content`` value into turns, block by block."""
    stamp = timestamp

    if isinstance(content, str):
        return [Turn(role=role, content=content, timestamp=stamp, token_count=token_count)]
    if not isinstance(content, list):
        return []

    turns: list[Turn] = []
    for block in content:
        if not isinstance(block, dict):
            skipped_blocks["non-object"] += 1
            continue
        block_type = block.get("type")
        if block_type == "text":
            turns.append(
                Turn(
                    role=role,
                    content=_as_text(block.get("text")),
                    timestamp=stamp,
                    token_count=token_count,
                )
            )
        elif block_type == "thinking":
            turns.append(
                Turn(
                    role=role,
                    reasoning=_as_text(block.get("thinking") or block.get("text")),
                    timestamp=stamp,
                    token_count=token_count,
                )
            )
        elif block_type == "tool_use":
            name = block.get("name")
            arguments = block.get("input")
            turns.append(
                Turn(
                    role=TurnRole.tool_call,
                    tool_name=name if isinstance(name, str) and name else "unknown_tool",
                    tool_args=arguments if isinstance(arguments, dict) else None,
                    tool_id=block.get("id") if isinstance(block.get("id"), str) else None,
                    timestamp=stamp,
                )
            )
        elif block_type == "tool_result":
            turns.append(
                Turn(
                    role=TurnRole.tool_result,
                    content=_flatten_tool_result(block.get("content")),
                    tool_id=(
                        block.get("tool_use_id")
                        if isinstance(block.get("tool_use_id"), str)
                        else None
                    ),
                    timestamp=stamp,
                )
            )
        else:
            skipped_blocks[str(block_type)] += 1
    return turns


def _flatten_tool_result(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(_as_text(block.get("text")))
            elif isinstance(block, dict):
                parts.append(json.dumps(block, sort_keys=True, default=str))
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    return json.dumps(content, sort_keys=True, default=str)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)
