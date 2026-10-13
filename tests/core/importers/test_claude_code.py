"""Claude Code importer tests.

Fixtures are synthetic; the tool-call block shape is covered by a fixture
rather than by observed data (see docs/AGENT_CLIS.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.conftest import fixture_path

from jev_harness.core.importers import ClaudeCodeImporter, ImporterError, SessionRef
from jev_harness.core.trace import Trace, TurnRole


def importer(root: Path | None = None, **kwargs: object) -> ClaudeCodeImporter:
    return ClaudeCodeImporter(root or fixture_path("claude_code"), **kwargs)  # type: ignore[arg-type]


def load(name: str) -> Trace:
    return importer().load_path(fixture_path("claude_code", name))


def test_basic_transcript_parses_in_file_order() -> None:
    trace = load("demo-basic.jsonl")
    assert trace.agent == "claude_code"
    assert trace.agent_version == "2.1.221"
    assert trace.session_id == "demo-basic"
    assert [turn.role for turn in trace.turns] == [
        TurnRole.user,
        TurnRole.system,
        TurnRole.assistant,
    ]
    assert [turn.index for turn in trace.turns] == [0, 1, 2]
    assert trace.turns[2].content == "The codeword is CANARY_BASIC."
    assert trace.turns[2].token_count == 42
    assert trace.turns[0].timestamp == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_basic_transcript_reports_what_it_skipped() -> None:
    meta = load("demo-basic.jsonl").meta
    assert meta["skipped_record_types"] == {"custom-title": 1, "queue-operation": 1}
    assert meta["lines_read"] == 5
    assert meta["unparseable_records"] == 0
    assert meta["tool_blocks_observed"] is False
    assert meta["usage"] == {
        "input_tokens": 1200,
        "output_tokens": 42,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 10,
    }


def test_tool_calls_results_and_thinking_are_separate_turns() -> None:
    trace = load("demo-tools.jsonl")
    assert [turn.role for turn in trace.turns] == [
        TurnRole.user,
        TurnRole.assistant,  # thinking block
        TurnRole.tool_call,
        TurnRole.tool_result,
        TurnRole.assistant,  # final text
    ]
    thinking = trace.turns[1]
    assert thinking.content == ""
    assert thinking.reasoning == "The user wants a cleanup; check the script first."

    call = trace.turns[2]
    assert call.tool_name == "Bash"
    assert call.tool_args == {"command": "rm -rf build"}
    assert call.tool_id == "toolu_01"

    result = trace.turns[3]
    assert result.tool_id == "toolu_01"
    assert result.content.startswith("removed build")
    assert "iVBORw0KGgo=" in result.content


def test_sidechain_and_junk_records_are_counted_not_silently_dropped() -> None:
    meta = load("demo-tools.jsonl").meta
    assert meta["sidechain_records_skipped"] == 1
    assert meta["unparseable_records"] == 1
    assert meta["skipped_record_types"] == {"attachment": 1}
    assert meta["skipped_block_types"] == {"mystery_block": 1}
    assert meta["tool_blocks_observed"] is True


def test_sidechain_can_be_included_explicitly() -> None:
    trace = importer(include_sidechain=True).load_path(
        fixture_path("claude_code", "demo-tools.jsonl")
    )
    assert any(turn.content == "subagent chatter" for turn in trace.turns)
    assert trace.meta["sidechain_records_skipped"] == 0


def test_a_transcript_with_no_conversation_raises_instead_of_scoring_zero() -> None:
    with pytest.raises(ImporterError) as error:
        load("demo-none.jsonl")
    message = str(error.value)
    assert "produced no turns" in message
    assert "attachment" in message


def test_load_resolves_a_session_id_and_a_path() -> None:
    assert importer().load("demo-basic").session_id == "demo-basic"
    by_path = importer().load(str(fixture_path("claude_code", "demo-basic.jsonl")))
    assert by_path.session_id == "demo-basic"


def test_missing_session_names_what_exists() -> None:
    with pytest.raises(ImporterError, match="no Claude Code session"):
        importer().load("does-not-exist")


def test_list_sessions_reads_metadata_without_parsing_conversations() -> None:
    refs = importer().list_sessions()
    by_id = {ref.id: ref for ref in refs}
    assert "demo-basic" in by_id
    assert by_id["demo-basic"].title == "basic session"
    assert by_id["demo-basic"].cwd == "/tmp/demo"
    assert by_id["demo-basic"].started_at == datetime(2026, 1, 2, 3, 4, 0, tzinfo=UTC)
    assert by_id["demo-basic"].size_bytes is not None
    assert str(by_id["demo-basic"]).startswith("claude_code:demo-basic (2026-01-02)")


def test_list_sessions_respects_limit_and_missing_root(tmp_path: Path) -> None:
    assert len(importer().list_sessions(limit=1)) == 1
    missing = ClaudeCodeImporter(root=tmp_path / "nope")
    assert missing.list_sessions() == []
    assert missing.available() is False


def test_load_accepts_a_session_ref(tmp_path: Path) -> None:
    path = tmp_path / "one.jsonl"
    path.write_text(fixture_path("claude_code", "demo-basic.jsonl").read_text())
    ref = SessionRef(id="demo-basic", agent="claude_code", path=path)
    assert importer().load(ref).session_id == "demo-basic"
    with pytest.raises(ImporterError, match="no path to read"):
        importer().load(SessionRef(id="x", agent="claude_code"))


def test_empty_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n")
    with pytest.raises(ImporterError, match="produced no turns"):
        importer().load_path(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ImporterError, match="transcript not found"):
        importer().load_path(tmp_path / "absent.jsonl")


def test_string_content_records_become_single_turns(tmp_path: Path) -> None:
    path = tmp_path / "strings.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "plain string"},
                "sessionId": "strings",
            }
        )
        + "\n"
    )
    trace = importer().load_path(path)
    assert len(trace.turns) == 1
    assert trace.turns[0].content == "plain string"


def test_records_without_a_message_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "odd.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "system", "sessionId": "odd"}),
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": "kept"},
                        "sessionId": "odd",
                    }
                ),
            ]
        )
        + "\n"
    )
    trace = importer().load_path(path)
    assert trace.meta["skipped_record_types"] == {"system:no-message": 1}
    assert len(trace.turns) == 1
