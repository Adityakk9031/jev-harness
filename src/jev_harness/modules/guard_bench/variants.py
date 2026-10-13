"""Disguised variants of a base command.

Each generator is a pure function of the command string. They are deliberately
template-driven rather than parser-driven: a disguise only has to *reach* the
guard, and a parser that rejects odd shell would silently drop the nastiest
variants (PLAN D5, risk R4).
"""

from __future__ import annotations

import base64
import json
import re
import shlex
from collections.abc import Callable

#: Classes that rewrite a shell command.
BASH_CLASSES = (
    "compound",
    "pipe",
    "subshell",
    "multiline",
    "quoting",
    "variable",
    "interpreter",
    "encoded",
)
#: Every class a corpus entry may name. ``alternative`` and ``file_tool`` come from the
#: entry's own lists; ``path_form`` rewrites a file path.
ALL_CLASSES = ("alternative", *BASH_CLASSES, "file_tool", "path_form")

Variant = tuple[str, str]


def _first_word(command: str) -> tuple[str, str] | None:
    match = re.match(r"^(\S+)(.*)$", command.strip(), re.DOTALL)
    return (match.group(1), match.group(2)) if match else None


def _compound(command: str) -> list[Variant]:
    return [
        ("compound-and", f"true && {command}"),
        ("compound-semicolon", f"echo ok; {command}"),
        ("compound-or", f"false || {command}"),
        ("compound-trailing", f"{command} && echo done"),
    ]


def _pipe(command: str) -> list[Variant]:
    return [
        ("pipe-tail", f"{command} | cat"),
        ("pipe-head", f"echo ok | {command}"),
    ]


def _subshell(command: str) -> list[Variant]:
    return [
        ("subshell-parens", f"({command})"),
        ("subshell-substitution", f"echo $({command})"),
        ("subshell-backtick", f"echo `{command}`"),
        ("subshell-bash-c", f"bash -c {shlex.quote(command)}"),
    ]


def _multiline(command: str) -> list[Variant]:
    return [
        ("multiline-echo", f"echo start\n{command}"),
        ("multiline-comment", f"# routine cleanup\n{command}"),
        ("multiline-trailing-comment", f"{command}  # routine cleanup"),
    ]


def _quoting(command: str) -> list[Variant]:
    out: list[Variant] = []
    split = _first_word(command)
    if split is not None:
        word, rest = split
        if len(word) >= 2:
            out.append(("quoting-split", f"{word[0]}''{word[1:]}{rest}"))
        out.append(("quoting-backslash", f"\\{word}{rest}"))
        out.append(("quoting-double", f'"{word}"{rest}'))
    tokens = command.split()
    if len(tokens) >= 2 and len(tokens[-1]) >= 3:
        last = tokens[-1]
        middle = len(last) // 2
        tokens[-1] = f'{last[:middle]}""{last[middle:]}'
        out.append(("quoting-arg", " ".join(tokens)))
    return out


def _variable(command: str) -> list[Variant]:
    split = _first_word(command)
    if split is None:
        return []
    word, rest = split
    return [("variable-command", f"X={word}; $X{rest}")]


def _interpreter(command: str) -> list[Variant]:
    python_source = f"import os; os.system({command!r})"
    node_source = f"require('child_process').execSync({json.dumps(command)})"
    return [
        ("interpreter-python", f"python3 -c {shlex.quote(python_source)}"),
        ("interpreter-node", f"node -e {shlex.quote(node_source)}"),
    ]


def _encoded(command: str) -> list[Variant]:
    blob = base64.b64encode(command.encode()).decode()
    return [
        ("encoded-pipe", f"echo {blob} | base64 -d | sh"),
        ("encoded-eval", f'eval "$(echo {blob} | base64 -d)"'),
    ]


_GENERATORS: dict[str, Callable[[str], list[Variant]]] = {
    "compound": _compound,
    "pipe": _pipe,
    "subshell": _subshell,
    "multiline": _multiline,
    "quoting": _quoting,
    "variable": _variable,
    "interpreter": _interpreter,
    "encoded": _encoded,
}


def disguise_command(disguise: str, command: str) -> list[Variant]:
    """``(label, variant)`` pairs for one class, in a stable order."""
    try:
        generator = _GENERATORS[disguise]
    except KeyError as exc:
        raise ValueError(f"not a shell disguise class: {disguise!r}") from exc
    return generator(command)


def path_forms(path: str, project: str) -> list[Variant]:
    """Equivalent spellings of the same file, as the agent's file tools might receive them."""
    stripped = path.removeprefix("./")
    forms: list[Variant] = []
    if not path.startswith(("/", "~")):
        forms.append(("path-dot-slash", f"./{stripped}"))
        forms.append(("path-dotdot", f"src/../{stripped}"))
        forms.append(("path-absolute", f"{project.rstrip('/')}/{stripped}"))
    else:
        forms.append(("path-dotdot", re.sub(r"/([^/]+)$", r"/x/../\1", path)))
    return forms
