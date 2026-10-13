"""Blocks anything that mentions git or rm at all, benign or not."""

import json
import sys

data = json.load(sys.stdin)
command = data["tool_input"].get("command", "")
if "git" in command or "rm" in command:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "too broad",
    }}))  # fmt: skip
