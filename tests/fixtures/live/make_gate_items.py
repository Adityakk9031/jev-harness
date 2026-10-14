"""Generate labelled CI-gate items for a live Jev calibration run (deterministic, seeded).

Question put to the judge: "Does this pull request need the full test suite?"
Ground truth (the label) comes from a rule applied to hidden structure, not from the title:

    skip = nothing that can change runtime behaviour was touched (docs, comments, licence, ignore files)
    full = anything else (code, dependencies, build/CI config, migrations, renames, type changes)

Hard cases are included on purpose, because calibration only shows up where the answer is not
obvious: harmless-sounding titles that touch code, and docs-looking changes in non-docs files.
Writes JSONL with id, title, files, the label, and a `kind` for analysis. The judge must not see
`kind`: run the gate with `--hide kind`.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

MODULES = [
    "payments",
    "auth",
    "orders",
    "search",
    "billing",
    "users",
    "reports",
    "cache",
    "queue",
    "api",
]
NOUNS = [
    "retry",
    "timeout",
    "parser",
    "serializer",
    "validator",
    "router",
    "scheduler",
    "importer",
    "exporter",
    "limiter",
]

DOCS_TITLES = [
    "Fix typo in README",
    "Update install instructions",
    "Clarify {m} section in docs",
    "Add FAQ entry about {n}",
    "Reword contributing guide",
    "Update screenshots description",
    "Fix broken link in docs/{m}.md",
    "Document the {n} option",
]
DOCS_FILES = [
    "README.md",
    "docs/{m}.md",
    "CONTRIBUTING.md",
    "docs/faq.md",
    "CHANGELOG.md",
    "docs/guide/{n}.md",
]
LICENSE_TITLES = [
    "Bump copyright year",
    "Update LICENSE holder name",
    "Add SPDX header note to NOTICE",
]
IGNORE_TITLES = [
    "Ignore .DS_Store",
    "Add editor folders to .gitignore",
    "Update .gitattributes line endings",
]
COMMENT_TITLES = [
    "Fix comment typo in {m}",
    "Reword docstring in {m}/{n}.py",
    "Correct misleading comment in {n}",
    "Add explanatory comment to {n} helper",
]
CODE_TITLES = [
    "Rewrite {n} logic in {m}",
    "Fix off-by-one in {m} {n}",
    "Change {m} {n} defaults",
    "Handle empty input in {m} {n}",
    "Add caching to {m} {n}",
    "Switch {m} to use new {n} client",
    "Fix race in {m} {n}",
    "Increase {n} limit for {m}",
]
DEP_TITLES = [
    "Bump {n}-lib to 2.4.1",
    "Update lockfile",
    "Pin requests to 2.31",
    "Upgrade framework to next major",
]
CI_TITLES = [
    "Update CI cache key",
    "Add Python 3.12 to test matrix",
    "Change Dockerfile base image",
    "Adjust deploy workflow timeout",
]
MIGRATION_TITLES = ["Add migration for {m} table", "Backfill {m} column", "Drop unused {m} index"]
HARD_FULL_TITLES = [
    "Rename variable in {m}/{n}.py for clarity",
    "Refactor {m} {n} (no functional change)",
    "Add type hints to {m}/{n}.py",
    "Reformat {m}/{n}.py with black",
    "Tidy imports in {m}/{n}.py",
    "Remove dead code from {m} {n}",
    "Update error message text in {m} {n}",
]
HARD_SKIP_TITLES = [
    "Update docs and fix spelling in {n}",
    "Docs: explain the {m} {n} flow",
    "Fix typo in comment above the {n} call",
    "Update README badges and links",
]


def make(seed: int = 7, per_kind: int = 50) -> list[dict[str, object]]:
    rng = random.Random(seed)  # noqa: S311 - reproducible, not security
    items: list[dict[str, object]] = []

    def fill(template: str) -> str:
        return template.format(m=rng.choice(MODULES), n=rng.choice(NOUNS))

    def add(
        kind: str, label: str, titles: list[str], files: list[str], summary: str, count: int
    ) -> None:
        for _ in range(count):
            n = rng.randint(1, 3)
            picked = rng.sample(files, min(n, len(files)))
            items.append(
                {
                    "id": f"{kind}-{len(items):04d}",
                    "title": fill(rng.choice(titles)),
                    "files": [fill(f) for f in picked],
                    "label": label,
                    "kind": kind,
                }
            )

    add(
        "docs",
        "skip",
        DOCS_TITLES,
        DOCS_FILES,
        "Only prose in documentation files changed.",
        per_kind,
    )
    add(
        "legal",
        "skip",
        LICENSE_TITLES,
        ["LICENSE", "NOTICE", "AUTHORS"],
        "Only licence or attribution text changed.",
        per_kind // 5,
    )
    add(
        "ignore",
        "skip",
        IGNORE_TITLES,
        [".gitignore", ".gitattributes", ".editorconfig"],
        "Only repository ignore or editor settings changed.",
        per_kind // 5,
    )
    add(
        "comment",
        "skip",
        COMMENT_TITLES,
        ["src/{m}/{n}.py", "src/{m}/service.py", "lib/{n}.js"],
        "Only comments or docstrings changed; no executable line changed.",
        per_kind,
    )
    add(
        "code",
        "full",
        CODE_TITLES,
        ["src/{m}/{n}.py", "src/{m}/service.py", "lib/{n}.js", "src/{m}/models.py"],
        "Executable source code changed.",
        per_kind,
    )
    add(
        "deps",
        "full",
        DEP_TITLES,
        ["requirements.txt", "package-lock.json", "pyproject.toml", "poetry.lock"],
        "Dependency versions changed.",
        per_kind // 2,
    )
    add(
        "ci",
        "full",
        CI_TITLES,
        [".github/workflows/ci.yml", "Dockerfile", "tox.ini", "Makefile"],
        "Build or CI configuration changed.",
        per_kind // 2,
    )
    add(
        "migration",
        "full",
        MIGRATION_TITLES,
        ["migrations/{n}.sql", "db/migrate/0042_{m}.rb"],
        "Database schema or data changed.",
        per_kind // 2,
    )
    add(
        "hard_full",
        "full",
        HARD_FULL_TITLES,
        ["src/{m}/{n}.py", "src/{m}/service.py"],
        "Source files changed; the author says behaviour is unchanged.",
        per_kind,
    )
    add(
        "hard_skip",
        "skip",
        HARD_SKIP_TITLES,
        ["docs/{m}.md", "README.md", "docs/guide/{n}.md"],
        "Only documentation changed, though the title mentions code concepts.",
        per_kind // 2,
    )
    rng.shuffle(items)
    for index, item in enumerate(items):
        item["id"] = f"pr-s{seed}-{index:04d}"
    return items


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "gate_items.jsonl")
    rows = make(seed=int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    skips = sum(r["label"] == "skip" for r in rows)
    print(f"wrote {out}: {len(rows)} items ({skips} skip, {len(rows) - skips} full)")
