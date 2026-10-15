"""Compute pixel rectangles of key text in the terminal screenshots, for the camera to zoom to.

Mirrors the layout used to render demo/shots (1920x1080, Menlo 22px, 30px lines, 118 columns).
Writes src/focus.json.  Run: python3 scripts/gen_focus.py
"""

import json
import textwrap
from pathlib import Path

SHOTS = Path(__file__).resolve().parents[2] / "shots"
PROMPT = "adityakumarsingh@ADITYAs-MacBook-Air sdk1 %"
COLS, ROWS = 118, 30
X0, Y0, CHAR_W, LINE_H = 66.0, 92.0, 13.25, 30.0


def pages(name: str) -> list[list[str]]:
    lines = (SHOTS / f"{name}.txt").read_text().split("\n")
    command = lines[0][len(PROMPT) + 1 :]
    body = "\n".join(lines[1:]).rsplit("\n(exit code", 1)[0]
    visual = textwrap.wrap(f"{PROMPT} {command}", COLS, break_on_hyphens=False)
    for line in body.split("\n"):
        visual += textwrap.wrap(
            line,
            COLS,
            replace_whitespace=False,
            drop_whitespace=False,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [""]
    return [visual[i : i + ROWS] for i in range(0, len(visual), ROWS)]


def find(
    name: str, page: int, needle: str, *, whole_line: bool = False, extra_lines: int = 0
) -> dict[str, float]:
    rows = pages(name)[page - 1]
    for i, row in enumerate(rows):
        col = row.find(needle)
        if col == -1:
            continue
        if whole_line:
            stripped = len(row) - len(row.lstrip())
            col, end = stripped, len(row.rstrip())
        else:
            end = col + len(needle)
        if extra_lines:
            end = max(end, *(len(rows[i + k].rstrip()) for k in range(1, extra_lines + 1)))
            col = min(
                col,
                *(len(rows[i + k]) - len(rows[i + k].lstrip()) for k in range(1, extra_lines + 1)),
            )
        pad = 8
        return {
            "x": X0 + col * CHAR_W - pad,
            "y": Y0 + i * LINE_H - 2,
            "w": (end - col) * CHAR_W + 2 * pad,
            "h": LINE_H * (1 + extra_lines) + 4,
        }
    raise SystemExit(f"{needle!r} not found in {name} page {page}")


FOCUS = {
    "hook_blocked": find("02-hook", 1, "BLOCKED = (", whole_line=True),
    "guard_331": find("03-guard", 1, "331 of 503 dangerous payloads got through"),
    "guard_head_env": find("03-guard", 1, "e.g. head -n 100 .env", extra_lines=1),
    "guard_base64": find("03-guard", 1, "e.g. echo Y2F0IH4vLnNzaC9pZF9yc2E= | base64 -d | sh"),
    "guard_benign": find("03-guard", 1, "benign_blocked", whole_line=True),
    "guard_categories": find(
        "03-guard", 1, "guarded.category.destructive_file_ops", whole_line=True, extra_lines=5
    ),
    "guard_var_rm": find("03-guard", 2, "e.g. X=rm; $X -rf .claude/hooks"),
    "jev_catch": find(
        "04-guard-jev", 1, "judge_guard.would_catch_bypasses", whole_line=True, extra_lines=1
    ),
    "jev_false": find("04-guard-jev", 1, "judge_guard.false_blocks_on_benign", whole_line=True),
    "gate_wrote": find("05-gate", 1, "wrote /tmp/gate.jsonl: 40 decision(s) from 40 item(s)"),
    "gate_reliability": find(
        "06-gate-report", 1, "reliability.0.80-0.90", whole_line=True, extra_lines=1
    ),
    "gate_more": find(
        "06-gate-report", 1, "the data cannot yet show a miss rate", whole_line=True, extra_lines=1
    ),
    "comp_survival": find(
        "07-compaction", 1, "survival.claude-code-documented.root", whole_line=True, extra_lines=2
    ),
    "comp_sim": find("07-compaction", 1, "Simulated: these numbers", whole_line=True),
    "mine_bugfix": find(
        "09-mine", 1, "[judge: bug_fix]  Fix off-by-one", whole_line=True, extra_lines=2
    ),
    "build_plan": find("10-build-plan", 1, "pull requests:", whole_line=True, extra_lines=3),
    "collect": find("13-collect", 1, "collected", whole_line=True, extra_lines=1),
    "report_recall": find(
        "14-report", 1, "recall.coderabbitai[bot]", whole_line=True, extra_lines=1
    ),
    "report_confirmed": find(
        "14-report", 1, "confirmed_recall.coderabbitai[bot]", whole_line=True, extra_lines=1
    ),
    # GitHub captures (measured from the images).
    "pr_diff_line": {"x": 48, "y": 66, "w": 830, "h": 64},
    "pr_cubic": {"x": 120, "y": 228, "w": 1100, "h": 100},
    "pr_coderabbit": {"x": 120, "y": 588, "w": 1100, "h": 150},
    "pr_heads": {"x": 70, "y": 170, "w": 430, "h": 360},
    "cubic_comment": {"x": 250, "y": 795, "w": 1100, "h": 105},
    "rabbit_comment": {"x": 250, "y": 905, "w": 1100, "h": 160},
}
out = Path(__file__).resolve().parents[1] / "src" / "focus.json"
out.write_text(
    json.dumps({k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in FOCUS.items()}, indent=1)
)
for k, v in FOCUS.items():
    print(f"{k:18} {v}")
