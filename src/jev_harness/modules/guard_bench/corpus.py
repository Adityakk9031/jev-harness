"""The payload corpus: base intents from YAML, expanded into disguised variants.

Payloads are *strings*. Nothing in this module (or the package) runs one in a
shell; they only ever reach a guard as the ``tool_input`` of a hook JSON.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jev_harness.core.errors import ConfigError
from jev_harness.modules.guard_bench import variants

ToolName = Literal["Bash", "Read", "Write", "Edit"]
Expected = Literal["block", "allow"]

CORPUS_VERSION = 1
BENIGN_CATEGORY = "benign_control"
CATEGORY_PATTERN = r"^[a-z][a-z0-9_]*$"


class EquivalentTool(BaseModel):
    """The same intent expressed through the agent's own file tool."""

    model_config = ConfigDict(extra="forbid")

    tool: Literal["Read", "Write", "Edit"]
    path: str = Field(min_length=1)


class CorpusEntry(BaseModel):
    """One base intent, before disguising."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    category: str = Field(pattern=CATEGORY_PATTERN)
    intent: str = Field(min_length=1)
    expected: Expected = "block"
    tool: ToolName = "Bash"
    command: str | None = None
    path: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    equivalent_tools: list[EquivalentTool] = Field(default_factory=list)
    #: Restrict disguises to this list; ``None`` means the default set for the entry.
    disguises: list[str] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.tool == "Bash":
            if not self.command:
                raise ValueError(f"{self.id}: a Bash entry needs a command")
            if self.path:
                raise ValueError(f"{self.id}: a Bash entry takes a command, not a path")
        else:
            if not self.path:
                raise ValueError(f"{self.id}: a {self.tool} entry needs a path")
            if self.command or self.alternatives:
                raise ValueError(f"{self.id}: a {self.tool} entry takes a path only")
        if self.disguises is not None:
            unknown = sorted(set(self.disguises) - set(variants.ALL_CLASSES))
            if unknown:
                raise ValueError(
                    f"{self.id}: unknown disguise class(es) {unknown}; "
                    f"expected a subset of {list(variants.ALL_CLASSES)}"
                )
        return self


class CorpusFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    payloads: list[CorpusEntry]

    @model_validator(mode="after")
    def _version(self) -> Self:
        if self.version != CORPUS_VERSION:
            raise ValueError(
                f"unsupported corpus version {self.version}; this release reads version "
                f"{CORPUS_VERSION}"
            )
        return self


class Payload(BaseModel):
    """One concrete thing sent to a guard."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    base_id: str
    category: str
    intent: str
    disguise: str
    expected: Expected
    tool: ToolName
    tool_input: dict[str, Any]

    @property
    def text(self) -> str:
        """The command or path, as a human would read it."""
        return str(self.tool_input.get("command") or self.tool_input.get("file_path") or "")

    @property
    def is_base(self) -> bool:
        return self.id == self.base_id


class Corpus(BaseModel):
    """A set of base entries; :meth:`expand` yields every payload."""

    model_config = ConfigDict(extra="forbid")

    entries: list[CorpusEntry]
    sources: list[str] = Field(default_factory=list)

    def expand(self, project: str = ".") -> list[Payload]:
        payloads: list[Payload] = []
        for entry in self.entries:
            payloads.extend(_expand_entry(entry, project))
        return payloads

    def sha(self, project: str = "<project>") -> str:
        """A stable digest of every expanded payload, for ``--compare``.

        The project path is a placeholder by default so the same corpus hashes the same on a
        laptop and in CI, where the checkout lives somewhere else.
        """
        rows = [(p.id, p.expected, p.tool, p.tool_input) for p in self.expand(project)]
        blob = json.dumps(rows, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _file_input(tool: str, path: str) -> dict[str, Any]:
    if tool == "Read":
        return {"file_path": path}
    if tool == "Write":
        return {"file_path": path, "content": "jev-harness canary content"}
    return {"file_path": path, "old_string": "jev-canary-old", "new_string": "jev-canary-new"}


def _default_disguises(entry: CorpusEntry) -> list[str]:
    if entry.expected == "allow":
        return ["compound", "pipe", "multiline"] if entry.tool == "Bash" else ["path_form"]
    if entry.tool == "Bash":
        return [c for c in variants.ALL_CLASSES if c != "path_form"]
    return ["path_form"]


def _expand_entry(entry: CorpusEntry, project: str) -> list[Payload]:
    wanted = set(entry.disguises if entry.disguises is not None else _default_disguises(entry))
    out: list[Payload] = []
    seen: set[str] = set()

    def add(payload_id: str, disguise: str, tool: ToolName, tool_input: dict[str, Any]) -> None:
        key = json.dumps([tool, tool_input], sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        out.append(
            Payload(
                id=payload_id,
                base_id=entry.id,
                category=entry.category,
                intent=entry.intent,
                disguise=disguise,
                expected=entry.expected,
                tool=tool,
                tool_input=tool_input,
            )
        )

    if entry.tool == "Bash":
        command = str(entry.command)
        add(entry.id, "base", "Bash", {"command": command})
        if "alternative" in wanted:
            for index, alt in enumerate(entry.alternatives, start=1):
                add(f"{entry.id}~alternative-{index}", "alternative", "Bash", {"command": alt})
        for disguise in variants.BASH_CLASSES:
            if disguise not in wanted:
                continue
            for label, text in variants.disguise_command(disguise, command):
                add(f"{entry.id}~{label}", disguise, "Bash", {"command": text})
        if "file_tool" in wanted:
            for equivalent in entry.equivalent_tools:
                add(
                    f"{entry.id}~file-{equivalent.tool.lower()}",
                    "file_tool",
                    equivalent.tool,
                    _file_input(equivalent.tool, equivalent.path),
                )
                if "path_form" in wanted or "file_tool" in wanted:
                    for label, path in variants.path_forms(equivalent.path, project):
                        add(
                            f"{entry.id}~file-{equivalent.tool.lower()}-{label}",
                            "file_tool",
                            equivalent.tool,
                            _file_input(equivalent.tool, path),
                        )
    else:
        base_path = str(entry.path)
        add(entry.id, "base", entry.tool, _file_input(entry.tool, base_path))
        if "path_form" in wanted:
            for label, path in variants.path_forms(base_path, project):
                add(f"{entry.id}~{label}", "path_form", entry.tool, _file_input(entry.tool, path))
    return out


def _load_yaml(text: str, origin: str) -> CorpusFile:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{origin} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{origin} must be a mapping with 'version' and 'payloads'")
    try:
        return CorpusFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{origin} is not a valid corpus: {exc}") from exc


def default_corpus_text() -> str:
    return (
        resources.files("jev_harness.modules.guard_bench")
        .joinpath("data/default_corpus.yaml")
        .read_text(encoding="utf-8")
    )


def load_corpus(extra: list[Path] | None = None, *, include_default: bool = True) -> Corpus:
    """Load the default corpus plus any user files; ids must be unique across all of them."""
    entries: list[CorpusEntry] = []
    sources: list[str] = []
    if include_default:
        entries.extend(_load_yaml(default_corpus_text(), "default corpus").payloads)
        sources.append("default")
    for path in extra or []:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"cannot read corpus file {path}: {exc}") from exc
        entries.extend(_load_yaml(text, str(path)).payloads)
        sources.append(str(path))
    if not entries:
        raise ConfigError("no corpus entries: pass --corpus FILE or drop --no-default-corpus")
    ids = [e.id for e in entries]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ConfigError(f"duplicate corpus ids: {duplicates}")
    return Corpus(entries=entries, sources=sources)
