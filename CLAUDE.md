# CLAUDE.md — working notes for future sessions

`jev-harness`: a Python SDK + CLI that tests **an AI coding-agent configuration** (instruction files,
hooks, permission rules, AI reviewers, cheap gate models). v1 = shared `core` + 4 modules:
`guard_bench`, `gate_calibration`, `compaction_survival`, `reviewer_eval`.

Read `docs/PLAN.md` before starting work. It has the milestone order, the decisions already made
(D1–D13), the open questions (OQ-1…OQ-7), and the verified external interfaces (Appendix A). Do not
re-derive those interfaces from memory — re-verify against the docs URLs in Appendix A and record
what you depend on in `docs/AGENT_CLIS.md`.

---

## Commands (the only ones you need)

Local Python is **3.9.6**, which is below our floor. Always go through `uv`; never call bare
`python`/`pip`/`pytest`. `.python-version` pins 3.11.

```bash
uv sync --all-extras          # install dev + all optional extras
uv run pytest                 # unit tests only (live tests excluded by default)
uv run pytest -m live         # live tests: needs real keys/CLIs, never run in CI unit job
uv run pytest tests/core      # one subtree
uv run pytest -k guard -vv    # one area
uv run ruff check .           # lint
uv run ruff format .          # format (use --check in CI)
uv run mypy                    # files=src/jev_harness, strict override on core only
uv run jev-harness --help     # CLI smoke test
uv build                      # wheel/sdist
uv run pytest --snapshot-update   # only when the Markdown change is intentional; review the diff
```

Full pre-commit gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy &&
uv run pytest`.

Dev tooling lives in `[dependency-groups] dev`, so `uv sync` installs it without `--all-extras`;
`--all-extras` adds the optional extras used by the stats and judge paths.

---

## Non-negotiable invariants

These are the product's promises. A change that breaks one of them is a bug, even if tests pass.

1. **Never touch the user's real setup.** All mutation happens inside `core.workspace.Workspace`
   (temp copy or git worktree). Instruction files, hooks, settings, and the main branch are read-only.
   `Workspace` refuses a path that is the user's real working tree.
2. **Never execute a guard payload.** Payloads are strings. A payload reaches a process only as
   hook JSON on stdin to a guard the user configured, with a timeout.
3. **Rules have the final say.** `escalate` from any rule wins outright. A judge may add findings or
   adjust confidence; it may never turn an `escalate` into a pass.
4. **Fail safe.** Judge timeout, network error, invalid output, or confidence `< 0.7` ⇒ `escalate`.
   Never a silent pass. Never catch an exception and continue as if the answer were benign.
5. **No network in unit tests.** `respx` for httpx, recordings for agent runs, `@pytest.mark.live`
   for anything real. CI runs `-m "not live"`.
6. **No invented numbers.** No benchmark, performance, or price figure in code, docs, or reports
   unless a run in this repo produced it — or the user supplied it and it is labelled as such. A rate
   without `n` and an interval is not allowed in a report.
7. **Honest labelling.** Emulated permission matching is labelled an approximation everywhere.
   Simulated-compaction results are labelled and never mixed with real ones.
8. **Secrets.** Never log or serialize API keys or tokens. `.env` stays gitignored; `.env.example`
   documents the names. Redact key-shaped strings in report headers.
9. **Budget caps are enforced in core**, not per module: `--max-cost` / `--max-runs` abort with
   exit code 2. Anything paid or live supports `--dry-run` and prints an estimate first.
10. **Never push** to a repository the user has not named and confirmed (`--target-repo` + prompt).

---

## Conventions

* Python 3.11+, `src/` layout, pydantic v2 models at every boundary (config, judge I/O, corpus, results).
* Async for all I/O (`httpx.AsyncClient`); the CLI owns the event loop, core functions are `async`.
* Public API of `core` is re-exported from `jev_harness.core`; modules import from there, never from
  deep private paths.
* Types: `mypy --strict` on `src/jev_harness/core` only. Elsewhere be disciplined anyway; `Any` at a
  boundary must be narrowed immediately.
* Errors: define a small typed exception per domain (`JevClientError`, `RunnerError`,
  `WorkspaceError`). Judge/API failures are data (→ `escalate`), not crashes; programmer errors are
  crashes.
* Exit codes: `0` clean, `1` fail-on condition met, `2` harness error. One place computes them.
* Findings use `Finding(rule_id, severity, message, location, evidence, confidence, source)` with
  `source ∈ {rule, judge}`. `rule_id` is stable, namespaced, and never renamed without a changelog
  entry — `--compare` and the Action depend on it.
* Reports: one `Result` model per module → `core.report.render(result, "json"|"md"|"github")`.
  Markdown is snapshot-tested with syrupy. Adding a section means updating the snapshot deliberately.
* CLI: Typer, one command module per feature, shared flags on a common callback. Every command has
  `--dry-run`, `--output`, `--judge`, `--config`, `--max-cost`, `--max-runs`, `--fail-on`.
* Do not name a function or variable `annotations`: `from __future__ import annotations` binds that
  name in every module (mypy catches it, and it reads as a redefinition).
* Check budget: **3–5 checks per module in v1.** Extra ideas go to `docs/ROADMAP.md`, not into code.
  (Exception: `reviewer.catch_not_confirmed` exists only when a judge runs.)
* Judges are advisory and go through `core.judging` (`JudgeBudget`, `ask_all`, `trusted`): reserve the
  whole batch before calling, treat escalated or low-confidence decisions as no answer, and never let a
  judge change a deterministic verdict.
* Every module ships a docs page under `docs/` (`GUARD.md`, `CALIBRATE.md`, `COMPACTION.md`,
  `REVIEWERS.md`) with: what it answers, quickstart, what it does not prove, and limitations.

---

## Repository layout

```
src/jev_harness/core/{trace,workspace,runner,judge,policy,stats,check,report,config,errors}.py
src/jev_harness/core/importers/{claude_code,codex,contextleleo}.py
src/jev_harness/modules/{guard_bench,gate_calibration,compaction_survival,reviewer_eval}/
src/jev_harness/cli.py
tests/{core,modules/<name>}/   tests/fixtures/<name>/   action/   .github/workflows/ci.yml
docs/{PLAN,ROADMAP,AGENT_CLIS,GUARD,CALIBRATE,COMPACTION,REVIEWERS}.md
```

## Fixture rules

Fixtures are realistic, **small**, and carry known answers. Each module's fixture set must include:

* at least one true-positive case (the thing the check exists to catch);
* at least one **must-not-flag** case (a benign control, a well-calibrated gate, a source that
  survives, a clean control PR);
* one case where the sample is too small, so the `too_few_samples` path is exercised.

Prefer generated fixtures with analytically known truth over hand-written expected numbers.

---

## Adding a check (recipe)

1. Add it to the module's fixtures first — including the case where it must stay silent.
2. Implement it as a `Rule` returning `Verdict | None`; `None` means "no opinion", not "pass".
3. If it consumes a judge, keep the rules-only path working and testable without any model.
4. Give it a stable `rule_id`, a severity, and a message that names the evidence (payload variant,
   source file, PR + line, threshold + interval).
5. If it emits a rate, attach `n` and an interval, and route small samples through
   `stats.too_few_samples`.
6. Update the module docs page and `docs/ROADMAP.md` (priorities shift as checks land).

---

## Definition of done per module

CLI runs on fixtures with known answers **including must-not-flag cases**; `--dry-run` works without
network or keys; `ruff` + `mypy` clean; whole suite green; docs page written; one real run pasted
into the PR/commit message as evidence. Report anything you could not run as a limitation — never as
a pass.

---

## Environment variables

`JEV_API_KEY` or `TYPESAFE_API_KEY`, `JEV_API_URL`, `JEV_MODEL`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GITHUB_TOKEN` (or `gh auth token`), `GITHUB_STEP_SUMMARY`. See `.env.example`.
Never read a key from a file the user did not name (`--env-file PATH` is how they name one), and
never print one. Jev is the primary judge; check a key with `jev-harness judge check`.

## Gotchas

* Local Python is 3.9 — `uv run` everything.
* Agent CLIs change: read `--help` at build time, record relied-on flags in `docs/AGENT_CLIS.md`,
  and fail loudly if expected output is missing instead of scoring zero.
* `bashlex` cannot parse all modern bash; the tokenizer fallback in the variant generator is not
  optional. Report unparsed constructs rather than guessing at them.
* Claude Code hook exit code `0` is **not** approval, and `PreToolUse` decisions live under
  `hookSpecificOutput.permissionDecision` while `PostToolUse`/`Stop` use `decision: "block"`.
* Do not commit, push, or open a PR unless the user asks. One logical change per commit, full suite
  before each.
