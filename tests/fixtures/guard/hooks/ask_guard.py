"""Asks a human about every command."""

import json
import sys

json.load(sys.stdin)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "confirm",
}}))  # fmt: skip
