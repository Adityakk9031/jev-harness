"""The canonical transcript model every module reads.

``Trace`` and ``Turn`` are the only representation of a session that modules
see, so that an agent's private format stops at the importer boundary. Two
fields go beyond the brief's list, and both are additive:

* ``Turn.reasoning`` - thinking blocks exist in real transcripts; folding them
  into ``content`` would silently mix private reasoning with the answer text
  that checks search, so they get their own field.
* ``Turn.tool_id`` - a tool call and its result cannot be paired without it, and
  the guard/reviewer modules care which result belongs to which call.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TurnRole(StrEnum):
    """Who produced a turn."""

    user = "user"
    assistant = "assistant"
    tool_call = "tool_call"
    tool_result = "tool_result"
    system = "system"


class Turn(BaseModel):
    """One message, tool call, tool result, or system record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: TurnRole
    content: str = ""
    reasoning: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_id: str | None = None
    timestamp: datetime | None = None
    token_count: int | None = Field(default=None, ge=0)
    index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _tool_fields_must_be_consistent(self) -> Self:
        if self.tool_args is not None and self.tool_name is None:
            raise ValueError("tool_args without a tool_name does not identify a call")
        if self.role is TurnRole.tool_call and self.tool_name is None:
            raise ValueError("a tool_call turn must name the tool it calls")
        if (
            self.role in (TurnRole.user, TurnRole.assistant, TurnRole.system)
            and self.tool_name is not None
        ):
            raise ValueError(f"a {self.role.value} turn must not carry a tool_name")
        return self

    def searchable_text(self) -> str:
        """Content plus reasoning: what a text probe is allowed to match."""
        return "\n".join(part for part in (self.content, self.reasoning) if part)


class Trace(BaseModel):
    """A parsed session, independent of the agent that produced it."""

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    agent_version: str | None = None
    turns: list[Turn] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    # ``Trace.slice`` is named by the public API; see the ruff builtins allowlist.
    def slice(self, start: int, stop: int | None = None) -> Trace:
        """Select turns by ``index``, half-open, like a sequence slice."""
        if start < 0 or (stop is not None and stop < start):
            raise ValueError(f"invalid turn range ({start}, {stop})")
        selected = [
            turn
            for turn in self.turns
            if turn.index >= start and (stop is None or turn.index < stop)
        ]
        return self.model_copy(update={"turns": selected})

    def to_messages(self) -> list[dict[str, Any]]:
        """A chat-shaped view of the trace.

        Tool turns keep their own roles plus ``tool_name``/``tool_args``/
        ``tool_id``. Providers that only accept user/assistant/tool triples
        should use :meth:`to_text` instead - this method does not pretend to be
        a provider-specific wire format.
        """
        messages: list[dict[str, Any]] = []
        for turn in self.turns:
            message: dict[str, Any] = {"role": turn.role.value, "content": turn.content}
            if turn.tool_name is not None:
                message["tool_name"] = turn.tool_name
            if turn.tool_args is not None:
                message["tool_args"] = turn.tool_args
            if turn.tool_id is not None:
                message["tool_id"] = turn.tool_id
            messages.append(message)
        return messages

    def to_text(
        self,
        *,
        include_reasoning: bool = False,
        max_chars_per_turn: int | None = None,
    ) -> str:
        """A readable projection, used for prompts and for free-text checks."""
        lines: list[str] = []
        for turn in self.turns:
            label = turn.role.value
            if turn.role is TurnRole.tool_call:
                arguments = json.dumps(turn.tool_args, sort_keys=True, default=str)
                body = f"{turn.tool_name}({arguments})"
            else:
                body = turn.content
            if include_reasoning and turn.reasoning:
                body = f"{body}\n[reasoning] {turn.reasoning}".strip()
            if max_chars_per_turn is not None and len(body) > max_chars_per_turn:
                body = f"{body[:max_chars_per_turn]}... [truncated]"
            lines.append(f"[{turn.index}] {label}: {body}")
        return "\n".join(lines)

    def contains(self, needle: str, *, case_sensitive: bool = False) -> bool:
        """Whether any turn's content or reasoning mentions ``needle``."""
        if not needle:
            raise ValueError("needle must not be empty")
        if case_sensitive:
            return any(needle in turn.searchable_text() for turn in self.turns)
        lowered = needle.lower()
        return any(lowered in turn.searchable_text().lower() for turn in self.turns)

    def tool_calls(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role is TurnRole.tool_call]

    def token_total(self) -> int:
        """Sum of the per-turn token counts the transcript actually reported."""
        return sum(turn.token_count or 0 for turn in self.turns)

    def has_token_counts(self) -> bool:
        return any(turn.token_count is not None for turn in self.turns)

    def with_reindexed_turns(self) -> Trace:
        """Renumber turns from zero, preserving order."""
        renumbered = [
            turn.model_copy(update={"index": position}) for position, turn in enumerate(self.turns)
        ]
        return self.model_copy(update={"turns": renumbered})

    def text_of_role(self, role: TurnRole) -> str:
        """Concatenated content of every turn with this role."""
        return "\n".join(turn.content for turn in self.turns if turn.role is role)
