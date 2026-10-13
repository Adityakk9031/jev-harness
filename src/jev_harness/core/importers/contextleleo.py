"""contextleleo importer: an optional, subprocess-based bridge.

contextleleo is a separate Rust CLI that reads coding-agent session history and
can write it out as a "Simple" interchange document. Its real interface (from
its own usage reference) is:

* ``contextleleo list [--from <harness>]`` - discovery, **human-readable only**;
  there is no documented machine-readable mode, so this importer deliberately
  does not scrape it.
* ``contextleleo export <id>[#range] [--from <harness>] [--out <file>]`` -
  writes the session as a Simple JSON document to stdout or ``--out``.

Consequences, chosen in PLAN OQ-2:

* ``list_sessions`` refuses rather than parsing human output.
* Sessions are loaded by explicit id, or from a Simple document on disk
  (which is also what ``export --out session.json`` produces, and what makes
  this path testable without the binary).
* Session ids can be read from a plain text file, one per line.

The document layout is ``{meta, messages}`` with typed blocks. Block and field
names are handled tolerantly and anything unrecognized is counted in
``Trace.meta``; a document that yields no turns raises :class:`ImporterError`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jev_harness.core.importers.base import ImporterError, SessionRef
from jev_harness.core.trace import Trace, Turn, TurnRole

DEFAULT_TIMEOUT_SECONDS = 30.0
STDERR_TAIL_CHARS = 300

SubprocessRunner = Callable[..., "subprocess.CompletedProcess[str]"]


class ContextleleoImporter:
    """Imports sessions by shelling out to ``contextleleo export``."""

    name = "contextleleo"

    def __init__(
        self,
        executable: str = "contextleleo",
        *,
        harness: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        runner: SubprocessRunner | None = None,
    ) -> None:
        self.executable = executable
        self.harness = harness
        self.timeout_seconds = timeout_seconds
        self._runner: SubprocessRunner = runner if runner is not None else subprocess.run

    # ------------------------------------------------------------ discovery

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def export_argv(self, session_id: str) -> list[str]:
        """The exact command used to export a session - asserted by tests."""
        argv = [self.executable, "export", session_id]
        if self.harness:
            argv.extend(["--from", self.harness])
        return argv

    def list_sessions(self, *, limit: int | None = None) -> list[SessionRef]:
        raise ImporterError(
            "contextleleo has no documented machine-readable `list` output, so this importer "
            "will not scrape its human-readable listing. Pass session ids explicitly "
            "(`--session-id`), give a file of ids, or point at a Simple document exported with "
            "`contextleleo export <id> --out session.json`."
        )

    def ids_from_file(self, path: Path) -> list[str]:
        """Read session ids from a text file, one per line; ``#`` starts a comment."""
        if not path.is_file():
            raise ImporterError(f"session id file not found: {path}")
        ids: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                ids.append(line)
        if not ids:
            raise ImporterError(f"{path} lists no session ids")
        return ids

    # -------------------------------------------------------------- parsing

    def load(self, session: SessionRef | str) -> Trace:
        if isinstance(session, SessionRef):
            if session.path is None:
                raise ImporterError(
                    f"session {session.id} has no file; contextleleo sessions must be exported "
                    "by id or read from a Simple document"
                )
            return self.load_document(session.path)

        candidate = Path(session).expanduser()
        if candidate.is_file():
            return self.load_document(candidate)
        return self.load_id(session)

    def load_id(self, session_id: str) -> Trace:
        argv = self.export_argv(session_id)
        if not self.available():
            raise ImporterError(
                f"{self.executable!r} is not on PATH; export the session yourself "
                f"(`{' '.join(argv)} --out session.json`) and pass that file instead"
            )

        try:
            completed = self._runner(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ImporterError(
                f"{' '.join(argv)} timed out after {self.timeout_seconds:g}s"
            ) from exc
        except OSError as exc:
            raise ImporterError(f"could not run {self.executable!r}: {exc}") from exc

        if completed.returncode != 0:
            tail = (completed.stderr or "").strip()[-STDERR_TAIL_CHARS:]
            raise ImporterError(
                f"{' '.join(argv)} exited {completed.returncode}" + (f": {tail}" if tail else "")
            )
        return self.parse_document(
            completed.stdout, source=f"{self.executable} export {session_id}"
        )

    def load_document(self, path: Path) -> Trace:
        if not path.is_file():
            raise ImporterError(f"document not found: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        # The document's own id wins; the file name is only a fallback.
        return self.parse_document(text, source=str(path), fallback_session_id=path.stem)

    def parse_document(
        self, text: str, *, source: str, fallback_session_id: str | None = None
    ) -> Trace:
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImporterError(f"{source} is not valid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise ImporterError(
                f"{source} must be a Simple document object, got {type(document).__name__}"
            )

        messages = document.get("messages")
        if not isinstance(messages, list):
            raise ImporterError(f"{source} has no `messages` list; is it a Simple document?")

        turns: list[Turn] = []
        skipped_blocks: Counter[str] = Counter()
        for message in messages:
            if not isinstance(message, dict):
                skipped_blocks["non-object-message"] += 1
                continue
            role = _role(message.get("role"))
            blocks = message.get("blocks", message.get("content"))
            turns.extend(_message_turns(role, blocks, skipped_blocks))

        if not turns:
            raise ImporterError(
                f"{source} produced no turns from {len(messages)} messages "
                f"(skipped blocks: {dict(sorted(skipped_blocks.items()))})"
            )

        meta = document.get("meta")
        renumbered = [
            turn.model_copy(update={"index": position}) for position, turn in enumerate(turns)
        ]
        return Trace(
            agent=self.name,
            session_id=_session_id(meta) or fallback_session_id or "contextleleo-session",
            turns=renumbered,
            meta={
                "source": source,
                "harness": self.harness,
                "document_meta": meta if isinstance(meta, dict) else {},
                "skipped_block_types": dict(sorted(skipped_blocks.items())),
            },
        )


def _session_id(meta: object) -> str | None:
    if isinstance(meta, dict):
        for key in ("id", "session_id", "sessionId"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _role(value: object) -> TurnRole:
    if isinstance(value, str):
        normalized = value.strip().lower()
        for role in TurnRole:
            if role.value == normalized:
                return role
        if normalized == "tool":
            return TurnRole.tool_call
    return TurnRole.user


def _message_turns(
    role: TurnRole,
    blocks: object,
    skipped_blocks: Counter[str],
) -> list[Turn]:
    if isinstance(blocks, str):
        return [Turn(role=role, content=blocks)]
    if not isinstance(blocks, list):
        return []

    turns: list[Turn] = []
    for block in blocks:
        if not isinstance(block, dict):
            skipped_blocks["non-object-block"] += 1
            continue
        block_type = block.get("type")
        if block_type == "text":
            turns.append(Turn(role=role, content=_as_text(block.get("text"))))
        elif block_type == "thinking":
            turns.append(
                Turn(role=role, reasoning=_as_text(block.get("thinking") or block.get("text")))
            )
        elif block_type in ("tool_use", "tool_call"):
            name = _tool_name(block)
            turns.append(
                Turn(
                    role=TurnRole.tool_call,
                    tool_name=name,
                    tool_args=_tool_args(block),
                    tool_id=_tool_id(block),
                )
            )
        elif block_type in ("tool_result", "tool_output"):
            turns.append(
                Turn(
                    role=TurnRole.tool_result,
                    content=_flatten(block.get("content") or block.get("output")),
                    tool_id=_tool_id(block),
                )
            )
        elif block_type == "image":
            turns.append(Turn(role=role, content="[image]"))
        else:
            skipped_blocks[str(block_type)] += 1
    return turns


def _tool_name(block: dict[str, Any]) -> str:
    for key in ("name", "tool_name", "tool"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown_tool"


def _tool_args(block: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("input", "arguments", "args", "parameters"):
        value = block.get(key)
        if isinstance(value, dict):
            return value
        if value is not None and not isinstance(value, (str, int, float, bool)):
            return None
    for key, value in block.items():
        if key in {"type", "id", "tool_use_id", "name", "tool_name", "tool"}:
            continue
        if isinstance(value, dict):
            return value
    return None


def _tool_id(block: dict[str, Any]) -> str | None:
    for key in ("id", "tool_use_id", "tool_id", "call_id"):
        value = block.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _flatten(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            _as_text(item.get("text")) if isinstance(item, dict) else _as_text(item)
            for item in value
        ]
        return "\n".join(part for part in parts if part)
    return _as_text(value)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)
