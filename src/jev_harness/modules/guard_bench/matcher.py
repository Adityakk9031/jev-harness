"""An emulated Claude Code permission matcher. **An approximation.**

It exists so the bench can say something about ``permissions.deny/ask/allow``
without launching the agent. It is not the agent's evaluator, and every report
that uses it says so. The assumptions it makes are listed in
:data:`ASSUMPTIONS` and copied into each report's limitations.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

import bashlex

from jev_harness.modules.guard_bench.corpus import Payload
from jev_harness.modules.guard_bench.decision import GuardDecision, Outcome
from jev_harness.modules.guard_bench.discovery import PermissionRule

GUARD_NAME = "permissions (emulated)"

ASSUMPTIONS = (
    "Permission rules are matched by an emulation, not by the agent's own evaluator; treat "
    "permission results as indicative and confirm any bypass in the real agent.",
    "Emulated Bash matching parses the command with bashlex, removes shell quoting, and checks "
    "each top-level command of a list, pipeline, or subshell. It does not look inside command "
    "substitution, backticks, `bash -c` strings, interpreter code, or variables, and a deny rule "
    "only matches when one such command matches.",
    "Emulated file matching normalises `..` segments and `~` before comparing, anchors "
    "relative patterns at the project root, and applies Edit rules to Write and the reverse. "
    "Read rules do not apply to shell commands such as `cat`.",
)


def _bash_segments(command: str) -> tuple[list[str], str]:
    """Top-level commands of a shell string, plus which parser produced them."""
    try:
        parsed = bashlex.parse(command)
    except Exception:  # bashlex raises assorted errors on constructs it cannot parse
        pieces = re.split(r"&&|\|\||;|\||\n", command)
        return [piece.strip() for piece in pieces if piece.strip()], "fallback"

    segments: list[str] = []

    def walk(node: Any) -> None:
        kind = getattr(node, "kind", None)
        if kind == "command":
            words = [p.word for p in node.parts if p.kind == "word"]
            if words:
                segments.append(" ".join(words))
            else:
                segments.extend(p.word for p in node.parts if p.kind == "assignment")
            return
        for attribute in ("parts", "list"):
            children = getattr(node, attribute, None)
            if isinstance(children, list):
                for child in children:
                    walk(child)
        child = getattr(node, "command", None)
        if child is not None and hasattr(child, "kind"):
            walk(child)

    for node in parsed:
        walk(node)
    return segments, "bashlex"


def _split_rule(spec: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"([A-Za-z_][\w:.-]*)(?:\((.*)\))?", spec.strip(), re.DOTALL)
    if match is None:
        return spec, None
    return match.group(1), match.group(2)


def _command_matches(pattern: str | None, segment: str) -> bool:
    if pattern is None or pattern == "":
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return segment == prefix or segment.startswith(prefix + " ")
    if "*" in pattern:
        regex = ".*".join(re.escape(part) for part in pattern.split("*"))
        return re.fullmatch(regex, segment, re.DOTALL) is not None
    return segment == pattern


def _glob_to_regex(glob: str) -> str:
    out = ""
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out += "(?:.*/)?"
            index += 3
        elif glob.startswith("**", index):
            out += ".*"
            index += 2
        elif glob[index] == "*":
            out += "[^/]*"
            index += 1
        elif glob[index] == "?":
            out += "[^/]"
            index += 1
        else:
            out += re.escape(glob[index])
            index += 1
    return out


class PermissionMatcher:
    """Evaluate allow/ask/deny rules against a payload (deny, then ask, then allow)."""

    def __init__(self, rules: list[PermissionRule], project: Path, home: Path | None) -> None:
        self.rules = rules
        self.project = project.resolve()
        self.home = (home or Path.home()).resolve()

    # ------------------------------------------------------------------ paths

    def _anchor(self, spec: str) -> str:
        if spec.startswith("//"):
            return spec[1:]
        if spec.startswith("~/"):
            return (
                posixpath.normpath(f"{self.home}/{spec[2:]}")
                if "*" not in spec
                else (f"{self.home}/{spec[2:]}")
            )
        if spec.startswith("/"):
            return f"{self.project}{spec}"
        return f"{self.project}/{spec.removeprefix('./')}"

    def _normalise(self, path: str) -> str:
        if path.startswith("~/"):
            path = f"{self.home}/{path[2:]}"
        elif not path.startswith("/"):
            path = f"{self.project}/{path}"
        return posixpath.normpath(path)

    def _path_matches(self, pattern: str | None, path: str) -> bool:
        if pattern is None or pattern == "":
            return True
        regex = _glob_to_regex(self._anchor(pattern))
        return re.fullmatch(regex + r"(?:/.*)?", self._normalise(path)) is not None

    # ---------------------------------------------------------------- decide

    def _rules_for(self, payload: Payload, kind: str) -> list[PermissionRule]:
        names = {"Bash": {"Bash"}, "Read": {"Read"}, "Write": {"Edit", "Write"}}.get(
            payload.tool, {"Edit", "Write"}
        )
        selected = []
        for rule in self.rules:
            if rule.kind != kind:
                continue
            if _split_rule(rule.spec)[0] in names:
                selected.append(rule)
        return selected

    def _hit(self, rule: PermissionRule, payload: Payload, segments: list[str]) -> bool:
        pattern = _split_rule(rule.spec)[1]
        if payload.tool == "Bash":
            return any(_command_matches(pattern, segment) for segment in segments)
        return self._path_matches(pattern, str(payload.tool_input.get("file_path", "")))

    def decide(self, payload: Payload) -> GuardDecision:
        segments: list[str] = []
        parser = "n/a"
        if payload.tool == "Bash":
            segments, parser = _bash_segments(str(payload.tool_input.get("command", "")))
        detail = {"parser": parser, "segments": segments}

        for kind, outcome in (("deny", Outcome.block), ("ask", Outcome.ask)):
            for rule in self._rules_for(payload, kind):
                if self._hit(rule, payload, segments):
                    return GuardDecision(
                        guard=GUARD_NAME,
                        kind="permissions",
                        outcome=outcome,
                        reason=f"{kind} rule {rule.spec}",
                        detail=detail,
                    )

        allow_rules = self._rules_for(payload, "allow")
        if payload.tool == "Bash" and segments:
            covered = all(
                any(_command_matches(_split_rule(r.spec)[1], segment) for r in allow_rules)
                for segment in segments
            )
            if covered and allow_rules:
                return GuardDecision(
                    guard=GUARD_NAME,
                    kind="permissions",
                    outcome=Outcome.allow,
                    reason="allowed by rule",
                    detail=detail,
                )
        elif any(self._hit(r, payload, segments) for r in allow_rules):
            return GuardDecision(
                guard=GUARD_NAME,
                kind="permissions",
                outcome=Outcome.allow,
                reason="allowed by rule",
                detail=detail,
            )
        return GuardDecision(
            guard=GUARD_NAME,
            kind="permissions",
            outcome=Outcome.no_opinion,
            reason="no rule matched",
            detail=detail,
        )
