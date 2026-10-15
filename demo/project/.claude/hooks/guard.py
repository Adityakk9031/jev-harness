"""A typical hand-written PreToolUse guard: block a list of known-bad strings.

This is the kind of hook many teams write first. The demo shows what it misses.
"""

import json
import sys

BLOCKED = ("rm -rf", "git push --force", "cat .env", "sudo ", "curl ", ".ssh/")

call = json.load(sys.stdin)
text = json.dumps(call.get("tool_input", {}))
for needle in BLOCKED:
    if needle in text:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"blocked: {needle!r}",
                    }
                }
            )
        )
        sys.exit(0)
