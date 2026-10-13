"""Codex importer tests.

The documented ``codex exec --json`` event stream is covered by a fixture. The
on-disk rollout format is deliberately unsupported, and a test pins that it
fails loudly rather than returning an empty transcript.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import fixture_path

from jev_harness.core.importers import CodexImporter, ImporterError, SessionRef
from jev_harness.core.trace import Trace, TurnRole


def importer(root: Path | None = None) -> CodexImporter:
    return CodexImporter(root or fixture_path("codex"))


def load(name: str) -> Trace:
    return importer().load_path(fixture_path("codex", name))


def test_event_stream_parses_into_turns() -> None:
    trace = load("exec-events.jsonl")
    assert trace.agent == "codex"
    assert trace.session_id == "0199a213-81c0-7800-8aa1-bbab2a035a53"
    assert trace.meta["thread_id"] == "0199a213-81c0-7800-8aa1-bbab2a035a53"
    assert [turn.role for turn in trace.turns] == [
        TurnRole.tool_call,
        TurnRole.tool_result,
        TurnRole.assistant,
        TurnRole.assistant,
        TurnRole.system,
    ]
    call, result, reasoning, answer, plan = trace.turns
    assert call.tool_name == "shell"
    assert call.tool_args == {"command": "bash -lc ls"}
    assert call.tool_id == "item_1"
    assert result.tool_id == "item_1"
    assert result.content == "docs\nsdk\n"
    assert reasoning.reasoning == "List the directories, then summarize."
    assert reasoning.content == ""
    assert answer.content == "Repo contains docs, sdk, and examples directories."
    assert plan.content == "1. inspect\n2. summarize"


def test_event_stream_reports_usage_and_unknown_items() -> None:
    meta = load("exec-events.jsonl").meta
    assert meta["usage"] == {
        "input_tokens": 24763,
        "cached_input_tokens": 24448,
        "output_tokens": 122,
    }
    assert meta["turns_started"] == 1
    assert meta["skipped_item_types"] == {"quantum_thing": 1}
    assert meta["unparseable_records"] == 0


def test_rollout_format_is_refused_with_the_skipped_types_named() -> None:
    with pytest.raises(ImporterError) as error:
        load("rollout-unknown.jsonl")
    message = str(error.value)
    assert "produced no turns" in message
    assert "response_item" in message
    assert "event_msg" in message
    assert "not supported yet" in message


def test_failed_turns_are_recorded_as_errors(tmp_path: Path) -> None:
    path = tmp_path / "failed.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"thread.started","thread_id":"t1"}',
                '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"partial"}}',
                '{"type":"turn.failed","message":"rate limited"}',
                '{"type":"error","message":"stream closed"}',
            ]
        )
        + "\n"
    )
    trace = importer().load_path(path)
    assert trace.meta["errors"] == ["rate limited", "stream closed"]


def test_started_agent_message_does_not_duplicate_the_answer(tmp_path: Path) -> None:
    path = tmp_path / "started.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"item.started","item":{"id":"i","type":"agent_message","text":""}}',
                '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"final"}}',
            ]
        )
        + "\n"
    )
    trace = importer().load_path(path)
    assert [turn.content for turn in trace.turns] == ["final"]


def test_list_sessions_uses_mtime_and_extracts_rollout_ids(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-2026-01-05T08-00-00-0199a213-81c0-7800-8aa1-bbab2a035a53.jsonl"
    rollout.write_text('{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\n')
    refs = importer(tmp_path).list_sessions()
    assert [ref.id for ref in refs] == ["0199a213-81c0-7800-8aa1-bbab2a035a53"]
    assert refs[0].started_at is not None
    assert refs[0].path == rollout


def test_missing_root_is_empty_not_an_error(tmp_path: Path) -> None:
    missing = CodexImporter(root=tmp_path / "nope")
    assert missing.list_sessions() == []
    assert missing.available() is False


def test_load_accepts_a_session_ref_without_a_path(tmp_path: Path) -> None:
    with pytest.raises(ImporterError, match="no path to read"):
        importer().load(SessionRef(id="x", agent="codex"))
    with pytest.raises(ImporterError, match="no Codex session"):
        importer().load("nope")
    by_path = importer().load(str(fixture_path("codex", "exec-events.jsonl")))
    assert by_path.agent == "codex"
    with pytest.raises(ImporterError, match="session file not found"):
        importer().load_path(tmp_path / "absent.jsonl")
