"""contextleleo importer tests.

The binary is not installed here, so the subprocess is stubbed. The exact argv
is asserted, because that is the part of this integration we depend on.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tests.conftest import FIXTURES_DIR, fixture_path

from jev_harness.core.importers import ContextleleoImporter, ImporterError, SessionRef
from jev_harness.core.trace import TurnRole

DOCUMENT = fixture_path("contextleleo", "simple-document.json")
MISSING_DOCUMENT = FIXTURES_DIR / "contextleleo" / "missing.json"
MISSING_IDS = FIXTURES_DIR / "contextleleo" / "missing.txt"


class FakeRunner:
    """Records argv and returns a canned result, so no binary is needed."""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


@pytest.fixture
def document_text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def importer(**kwargs: object) -> ContextleleoImporter:
    return ContextleleoImporter(**kwargs)  # type: ignore[arg-type]


def test_document_maps_every_block_type() -> None:
    trace = importer().load_document(DOCUMENT)
    assert trace.agent == "contextleleo"
    assert trace.session_id == "ctx-001"
    assert trace.meta["document_meta"]["harness"] == "claude_code"
    assert trace.meta["skipped_block_types"] == {"hologram": 1}
    assert [turn.role for turn in trace.turns] == [
        TurnRole.user,
        TurnRole.assistant,
        TurnRole.assistant,
        TurnRole.tool_call,
        TurnRole.tool_result,
        TurnRole.assistant,
    ]
    assert trace.turns[1].reasoning == "Consider the timeout."
    assert trace.turns[1].content == ""
    call = trace.turns[3]
    assert call.tool_name == "Bash"
    assert call.tool_args == {"command": "grep -n relay src/x.py"}
    assert call.tool_id == "c1"
    assert trace.turns[4].tool_id == "c1"
    assert trace.turns[4].content == "12: relay timeout"
    assert trace.turns[5].content == "[image]"


def test_document_id_falls_back_to_the_file_name(tmp_path: Path) -> None:
    path = tmp_path / "renamed-session.json"
    path.write_text('{"messages":[{"role":"user","blocks":[{"type":"text","text":"hi"}]}]}')
    assert importer().load_document(path).session_id == "renamed-session"


def test_export_argv_is_exactly_what_we_depend_on() -> None:
    assert importer().export_argv("abc123") == ["contextleleo", "export", "abc123"]
    assert importer(harness="claude_code").export_argv("abc123") == [
        "contextleleo",
        "export",
        "abc123",
        "--from",
        "claude_code",
    ]


def test_load_id_runs_export_and_parses_stdout(
    monkeypatch: pytest.MonkeyPatch, document_text: str
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/contextleleo")
    runner = FakeRunner(stdout=document_text)
    trace = importer(runner=runner, harness="claude_code").load_id("abc123")
    assert runner.calls == [["contextleleo", "export", "abc123", "--from", "claude_code"]]
    assert trace.session_id == "ctx-001"
    assert trace.meta["harness"] == "claude_code"


def test_load_id_without_the_binary_explains_the_alternative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(ImporterError) as error:
        importer().load_id("abc123")
    message = str(error.value)
    assert "not on PATH" in message
    assert "contextleleo export abc123 --out session.json" in message


def test_export_failure_reports_the_exit_code_and_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/contextleleo")
    runner = FakeRunner(stderr="error: no session matches 'zzz'", returncode=1)
    with pytest.raises(ImporterError, match="exited 1"):
        importer(runner=runner).load_id("zzz")


def test_export_timeout_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/contextleleo")

    def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    with pytest.raises(ImporterError, match="timed out after"):
        importer(runner=timeout, timeout_seconds=1).load_id("abc123")


def test_list_sessions_refuses_to_scrape_human_output() -> None:
    with pytest.raises(ImporterError, match="machine-readable"):
        importer().list_sessions()


def test_ids_can_come_from_a_file() -> None:
    ids = importer().ids_from_file(fixture_path("contextleleo", "ids.txt"))
    assert ids == ["abc123", "def456"]
    with pytest.raises(ImporterError, match="not found"):
        importer().ids_from_file(MISSING_IDS)


def test_empty_id_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "ids.txt"
    path.write_text("# nothing but comments\n\n")
    with pytest.raises(ImporterError, match="lists no session ids"):
        importer().ids_from_file(path)


def test_bad_documents_are_rejected_with_a_reason() -> None:
    with pytest.raises(ImporterError, match="not valid JSON"):
        importer().parse_document("{oops", source="inline")
    with pytest.raises(ImporterError, match="must be a Simple document object"):
        importer().parse_document("[1, 2]", source="inline")
    with pytest.raises(ImporterError, match="no `messages` list"):
        importer().parse_document('{"meta":{}}', source="inline")
    with pytest.raises(ImporterError, match="produced no turns"):
        importer().parse_document('{"messages":[{"role":"user"}]}', source="inline")
    with pytest.raises(ImporterError, match="document not found"):
        importer().load_document(MISSING_DOCUMENT)


def test_load_dispatches_files_ids_and_refs(
    monkeypatch: pytest.MonkeyPatch, document_text: str
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/contextleleo")
    runner = FakeRunner(stdout=document_text)

    assert importer(runner=runner).load(str(DOCUMENT)).session_id == "ctx-001"
    assert runner.calls == []  # a file never shells out
    assert importer(runner=runner).load("abc123").session_id == "ctx-001"
    assert runner.calls == [["contextleleo", "export", "abc123"]]

    with pytest.raises(ImporterError, match="must be exported"):
        importer(runner=runner).load(SessionRef(id="x", agent="contextleleo"))


def test_available_reflects_the_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert importer().available() is False
    monkeypatch.setattr(shutil, "which", lambda _name: sys.executable)
    assert importer().available() is True
