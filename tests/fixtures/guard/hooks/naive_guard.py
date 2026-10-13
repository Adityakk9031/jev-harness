"""A substring guard: it only recognises a few spellings. Exits 2 to block."""

import json
import sys

NEEDLES = ("rm -rf", "git push --force", "cat .env", "sudo ")

data = json.load(sys.stdin)
command = data["tool_input"].get("command", "") or data["tool_input"].get("file_path", "")
if any(needle in command for needle in NEEDLES):
    sys.stderr.write("naive guard: blocked a known-bad substring\n")
    sys.exit(2)
