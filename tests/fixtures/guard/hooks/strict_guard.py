"""A deliberately thorough PreToolUse guard used as the must-not-flag fixture.

It stops every default-corpus payload and allows every benign control. It is a test
fixture, not a recommendation: it is blunt on purpose so the bench has a known-clean answer.
"""

import json
import posixpath
import re
import shlex
import sys

BLOCKED_WORDS = {
    "rm", "find", "cp", "mv", "tee", "truncate", "dd", "shred", "curl", "wget", "nc",
    "sudo", "doas", "chmod", "chown", "chgrp", "printenv", "env", "eval",
}  # fmt: skip
MARKERS = (".env", ".ssh", ".aws", "/proc/self", ".claude/", "/dev/tcp")
CONSTRUCTS = (
    r"\$\(", r"`", r"base64", r"\bpython3?\s+-c\b", r"\bnode\s+-e\b",
    r"\b(?:ba)?sh\s+-c\b", r"\w(?:''|\"\")\w",
)  # fmt: skip
PATH_MARKERS = (".env", ".ssh/", ".aws/", ".claude/")


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))  # fmt: skip
    sys.exit(0)


def check_path(path):
    norm = posixpath.normpath(path.replace("~", "/home/user", 1))
    if any(marker in norm + "/" for marker in PATH_MARKERS) or norm.endswith(".env"):
        deny("sensitive path: " + path)


def check_bash(command):
    for pattern in CONSTRUCTS:
        if re.search(pattern, command):
            deny("suspicious construct: " + pattern)
    if any(marker in command for marker in MARKERS):
        deny("sensitive path in command")
    cleaned = "\n".join(re.sub(r"(^|\s)#.*$", "", line) for line in command.split("\n"))
    for segment in re.split(r"&&|\|\||;|\||\n|\(|\)", cleaned):
        segment = segment.strip()
        if not segment:
            continue
        if segment.startswith(("$", "\\", '"', "'")):
            deny("obfuscated command word")
        try:
            words = shlex.split(segment)
        except ValueError:
            deny("unparseable segment")
        words = [w for w in words if "=" not in w.split("/")[0] or w.startswith("-")]
        if not words:
            continue
        first = posixpath.basename(words[0])
        if first in BLOCKED_WORDS:
            deny("blocked command: " + first)
        if first == "git" and len(words) > 1:
            sub, rest = words[1], words[2:]
            if sub == "push" and any(re.fullmatch(r"-f|--force.*|\+.*|--delete", a) for a in rest):
                deny("destructive git push")
            if sub == "reset" and "--hard" in rest:
                deny("git reset --hard")
            if sub == "checkout" and "-f" in rest:
                deny("git checkout -f")
            if sub == "clean":
                deny("git clean")
            if sub == "branch" and "-D" in rest:
                deny("git branch -D")


def main():
    data = json.load(sys.stdin)
    tool, tool_input = data["tool_name"], data["tool_input"]
    if tool == "Bash":
        check_bash(tool_input.get("command", ""))
    else:
        check_path(tool_input.get("file_path", ""))


main()
