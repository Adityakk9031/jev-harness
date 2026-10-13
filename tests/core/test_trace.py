"""Trace and Turn tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jev_harness.core.trace import Trace, Turn, TurnRole


def make_trace() -> Trace:
    return Trace(
        agent="claude_code",
        agent_version="2.1.221",
        session_id="s1",
        turns=[
            Turn(role=TurnRole.user, content="read CLAUDE.md", index=0),
            Turn(
                role=TurnRole.assistant,
                reasoning="consider the canary",
                index=1,
                token_count=12,
            ),
            Turn(
                role=TurnRole.tool_call,
                tool_name="Read",
                tool_args={"file_path": "CLAUDE.md"},
                tool_id="call_1",
                index=2,
            ),
            Turn(role=TurnRole.tool_result, content="CANARY_ALPHA", tool_id="call_1", index=3),
            Turn(role=TurnRole.assistant, content="The canary is CANARY_ALPHA.", index=4),
        ],
        meta={"source": "fixture"},
    )


def test_tool_call_requires_a_tool_name() -> None:
    with pytest.raises(ValidationError, match="tool_call turn must name the tool"):
        Turn(role=TurnRole.tool_call)


def test_tool_args_without_a_tool_name_are_rejected() -> None:
    with pytest.raises(ValidationError, match="tool_args without a tool_name"):
        Turn(role=TurnRole.tool_result, tool_args={"command": "ls"})


def test_conversation_turns_must_not_carry_tool_names() -> None:
    with pytest.raises(ValidationError, match="must not carry a tool_name"):
        Turn(role=TurnRole.user, content="hi", tool_name="Read")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Turn(role=TurnRole.user, content="hi", surprise=True)


def test_turns_are_immutable() -> None:
    turn = Turn(role=TurnRole.user, content="hi")
    with pytest.raises(ValidationError, match="frozen"):
        turn.content = "changed"


def test_slice_is_half_open_and_keeps_other_fields() -> None:
    trace = make_trace()
    middle = trace.slice(1, 3)
    assert [turn.index for turn in middle.turns] == [1, 2]
    assert middle.session_id == trace.session_id
    assert middle.agent_version == "2.1.221"
    assert len(trace.slice(3).turns) == 2
    assert len(trace.slice(0, 99).turns) == 5


def test_slice_rejects_impossible_ranges() -> None:
    trace = make_trace()
    with pytest.raises(ValueError, match="invalid turn range"):
        trace.slice(-1)
    with pytest.raises(ValueError, match="invalid turn range"):
        trace.slice(3, 1)


def test_to_messages_preserves_tool_metadata() -> None:
    messages = make_trace().to_messages()
    assert messages[0] == {"role": "user", "content": "read CLAUDE.md"}
    assert messages[2] == {
        "role": "tool_call",
        "content": "",
        "tool_name": "Read",
        "tool_args": {"file_path": "CLAUDE.md"},
        "tool_id": "call_1",
    }
    assert messages[3]["tool_id"] == "call_1"


def test_to_text_renders_tool_calls_by_name_and_args() -> None:
    text = make_trace().to_text()
    assert '[2] tool_call: Read({"file_path": "CLAUDE.md"})' in text
    assert "[0] user: read CLAUDE.md" in text
    assert "consider the canary" not in text


def test_to_text_can_include_reasoning_and_truncate() -> None:
    with_reasoning = make_trace().to_text(include_reasoning=True)
    assert "[reasoning] consider the canary" in with_reasoning
    # "The canary is CANARY_ALPHA."[:15] == "The canary is C"
    assert "[4] assistant: The canary is C... [truncated]" in make_trace().to_text(
        max_chars_per_turn=15
    )


def test_contains_searches_content_and_reasoning() -> None:
    trace = make_trace()
    assert trace.contains("CANARY_ALPHA")
    assert trace.contains("canary_alpha")  # case-insensitive by default
    assert trace.contains("CANARY_ALPHA", case_sensitive=True)
    assert not trace.contains("canary_alpha", case_sensitive=True)
    assert trace.contains("consider the canary")  # reasoning counts
    with pytest.raises(ValueError, match="needle must not be empty"):
        trace.contains("")


def test_token_accounting_is_honest_about_what_it_knows() -> None:
    trace = make_trace()
    assert trace.token_total() == 12
    assert trace.has_token_counts()
    bare = Trace(agent="a", session_id="s", turns=[Turn(role=TurnRole.user, content="hi")])
    assert bare.token_total() == 0
    assert not bare.has_token_counts()


def test_tool_calls_and_text_of_role() -> None:
    trace = make_trace()
    assert [turn.tool_name for turn in trace.tool_calls()] == ["Read"]
    assert trace.text_of_role(TurnRole.user) == "read CLAUDE.md"
    assert trace.text_of_role(TurnRole.system) == ""


def test_with_reindexed_turns_renumbers_from_zero() -> None:
    trace = Trace(
        agent="a",
        session_id="s",
        turns=[
            Turn(role=TurnRole.user, content="a", index=7),
            Turn(role=TurnRole.assistant, content="b", index=9),
        ],
    )
    assert [turn.index for turn in trace.with_reindexed_turns().turns] == [0, 1]
    assert [turn.index for turn in trace.turns] == [7, 9]


def test_timestamp_round_trips() -> None:
    turn = Turn(
        role=TurnRole.user,
        content="hi",
        timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    assert turn.timestamp == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
