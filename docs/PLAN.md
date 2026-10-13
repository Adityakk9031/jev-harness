# jev-harness — v1 plan

Status: **awaiting author approval** (step 8.1 complete; no code written yet).
Owner: author (Aditya). Implementer: Claude Code in this repo.
Last updated: 2026-10-13.

---

## 1. What we are building

`jev-harness` is a Python SDK + CLI that tests **an AI coding-agent configuration**, not the
user's application code. v1 ships a shared `core` plus four modules, each answering one question
about a real `.claude/` / `AGENTS.md` / reviewer / gate setup:

| Module | Question | Needs a model? |
|---|---|---|
| `guard_bench` | Do my hooks and permission rules block *disguised* dangerous commands? | No |
| `gate_calibration` | When my cheap gate says 70%, is it right 70% of the time? | No |
| `compaction_survival` | Which of my instructions does the agent forget after compaction? | Yes (agent under test) |
| `reviewer_eval` | Which AI reviewer catches real bugs in *my* code, at what noise cost? | Yes (reviewers under test) |

Build order follows the brief: models/network-free first, external services last.

**Success for v1** = a developer can run one command per module against their own repo, get a
JSON/Markdown/step-summary report that is honest about sample size, and get a nonzero exit code when
something is provably broken.

---

## 2. Working agreement (how this gets built)

1. Stages below, in order. **No module starts before core passes its gate.**
2. Every stage ends with: `ruff` clean, `mypy` clean (strict on `core`), full test suite green,
   and a **real run on fixtures** whose expected answers are known. I run these myself and paste real
   output; I do not claim a pass I did not observe.
3. Small commits, one logical change each, full suite before each commit. I will not commit until
   you approve the plan; after that I commit per stage.
4. Per-module definition of done (from brief §8.3): CLI runs on fixtures with known answers;
   tests include true-positive **and must-not-flag** cases; `--dry-run` works; docs page exists;
   lint + types + tests clean.
5. Every number that reaches a doc or a report is produced by a run in this repo (brief §2.8).
   Where a figure cannot be measured yet, the docs say "not measured" rather than a made-up value.
6. I stop and ask when blocked on a *design* decision (see §10). Otherwise I choose the
   conventional option, record it here, and continue.

---

## 3. Decisions already made (with rationale)

| # | Decision | Rationale |
|---|---|---|
| D1 | Package `jev-harness` / import `jev_harness`, Python `>=3.11`, `uv`, `src/` layout | Brief §3. |
| D2 | Core has **no heavy third-party runtime deps**: Wilson intervals are closed-form and computed in pure Python. `[stats]` adds numpy+scipy for Clopper–Pearson, drift tests, and calibration tables. | Keeps `guard` (which needs no stats) installable without scipy; makes the extra in brief §3 meaningful instead of decorative. |
| D3 | `Judge` is asked a **single `choice` question per decision**, because Jev only returns a `confidence` for `choice`/`score` answers (see Appendix A.3). The chosen option is the answer; `confidence` is the calibration input. | Matches the API and gives the policy engine a calibrated number on every decision. |
| D4 | Jev adapter mirrors `jev-build-sentinel`'s client semantics: base `https://api.typesafe.ai/v1`, model `jev-latest`, retry on 408/429/500/502/503/504/**529**, no retry on 401/400/422, timeout, strict response validation, and **degrade to `escalate`** (never to pass). | Fail-safe (brief §2.6) and consistent with the author's existing tool. |
| D5 | Shell parsing: **`bashlex` as primary, with a small hand-rolled tokenizer fallback** that the variant generator can always use. bashlex gives an AST (compound commands, pipes, substitutions) which is what disguise analysis needs; it does not parse every construct of modern bash, so the generator's templates must not *depend* on it. Both paths are exercised by tests. | Brief §3 asks for a justification; this is it. Risk R4 in §11. |
| D6 | Permission-rule support in `guard_bench` v1 is **static analysis + an emulated matcher**, and every report labels it `"matcher": "emulated (approximation)"` with a Markdown banner. Live verification against the real agent is roadmap. | Brief §6.1; an emulated matcher that looks authoritative is worse than no matcher. |
| D7 | `guard_bench` v1 ships **Claude Code hooks + a generic stdin/stdout guard adapter**. Codex hooks are documented today but carry no explicit *Stable* maturity label and require per-project trust, so v1 documents them and puts them on the roadmap behind an opt-in flag. | Brief §6.1's condition ("only if documented and stable"); Appendix A.2.4 shows it is documented but not labelled stable. |
| D8 | `compaction_survival` ships a **simulated mode with a pluggable summarizer** as a first-class path, and treats real headless compaction as a spike-then-build item (M4.0). | No doc confirms `/compact` works as a `-p` prompt (Appendix A.1.4). Simulated mode is needed for offline tests regardless; real mode must not be faked. |
| D9 | Exit codes are uniform across modules: `0` clean, `1` fail-on condition met, `2` harness error (bad config, missing key, budget exhausted). | Testable, CI-friendly, and lets the Action map them to step failures. |
| D10 | Cost estimates are **not hardcoded prices**. `--dry-run` prints planned runs and token/character estimates; dollar estimates use the CLI's own reported `total_cost_usd` for actuals and a user-provided price table for preflight. | Brief §2.8 — no invented numbers, including prices that change. |
| D11 | Statistics: Wilson and Clopper–Pearson intervals, `too_few_samples` warnings, and two-rate comparison. Drift compares windows with a multiplicity correction (Holm–Bonferroni) because a per-threshold scan invites false positives. | Brief §2.7 + §6.2; the correction is a small honest addition. |
| D12 | Optional trace importers: **native Claude Code JSONL + Codex rollout importers first**, then `ContextleleoImporter` against the *real* documented interface (`contextleleo export <id>` → "Simple" JSON on stdout), never the assumed `--format json` form. | Appendix A.4 shows the real CLI. |
| D13 | Repository is git-initialized now; **nothing is committed until you approve this plan.** | Brief §8.4 needs history; your instructions say no commits without approval. |

---

## 4. Core spec (what the modules are allowed to depend on)

```
Trace(agent, agent_version, session_id, turns, meta)
  .slice(i, j) / .to_messages() / .token_total()
Turn(role: user|assistant|tool_call|tool_result|system,
     content, tool_name?, tool_args?, timestamp?, token_count?, index)
```

* `AgentRunner` — protocol; `ClaudeCodeRunner`, `CodexRunner`. `run(prompt, workspace, session=None)`
  → `RunResult(trace, cost_usd|None, exit_status, raw)`. Records to disk (`recording.json`) and
  replays; replay is the default in tests, live is `@pytest.mark.live`.
* `Workspace` — `Workspace.create(target_repo)` makes a temp copy or git worktree, applies planted
  files, and **refuses to operate on a path that is the user's real working tree** (checked via the
  git top-level of the cwd and an explicit `--i-know` escape hatch that is off by default).
  Cleanup is guaranteed via context manager + `atexit` sweep.
* `Judge` — `async decide(question: JudgeQuestion) -> Decision(answer, confidence, raw, latency_ms)`;
  adapters `JevJudge`, `AnthropicJudge`, `OpenAIJudge`, `NullJudge`. Retries on 408/429/5xx (+529
  for Jev), strict output validation, and **any error/timeout/invalid output ⇒ `escalate`**.
* `Policy` — `Rule.check(ctx) -> Verdict | None` where `Verdict ∈ {pass, flag, escalate}`.
  Precedence: any `escalate` wins; judge confidence `< 0.7` (configurable) escalates; every
  triggered rule records `rule_id` + reason; a judge can never override a rule `escalate`.
* `Check` / `Eval` / `Finding(rule_id, severity, message, location, evidence, confidence, source)`
  with `source ∈ {rule, judge}`.
* `stats` — `wilson(k, n)`, `clopper_pearson(k, n)`, `too_few_samples(n)`, `compare_rates(a, b)`.
* `report` — `render(result, fmt=json|md|github)`; Markdown is snapshot-tested with syrupy;
  `github` writes `$GITHUB_STEP_SUMMARY`-compatible Markdown.
* `config` — env + optional `jev-harness.yml` (pydantic-settings style, but see R6 on dependency
  justification).

**Core gate (brief §5):** public API fully tested, mypy-strict clean, importable with zero extras
installed.

---

## 5. Milestones and gates

| M | Content | Gate to move on |
|---|---|---|
| **M0** | Scaffold (`pyproject.toml`, extras, ruff, mypy, pytest config, `.env.example`, `.gitignore`), `config`, `stats`, `report`, CI workflow. | `uv sync`, `uv run pytest`, `ruff`, `mypy` green on 3.11 **and** 3.12; `uv build` produces a wheel; fresh-venv install works. |
| **M1** | Core: `trace`, `workspace`, `runner` (+recording/replay), `judge`, `policy`, `check`, importers (native). Includes a short spike to locate Claude Code and Codex session files empirically and record findings in `docs/AGENT_CLIS.md`. | Core gate above. |
| **M2** | `guard_bench` | §6.1 DoD. |
| **M3** | `gate_calibration` | §6.2 DoD. |
| **M4** | `compaction_survival` (M4.0 spike first) | §6.3 DoD. |
| **M5** | `reviewer_eval` | §6.4 DoD. |
| **M6** | Release: wheel, README quickstarts, composite Action for `guard` + `calibrate`, `CHANGELOG.md`, license, `1.0.0`. | Brief §9 checklist, executed on a clean venv and a throwaway repo. |

Relative size: M0 S, M1 L, M2 M, M3 M, M4 M/L, M5 L, M6 M. M5 is the riskiest (GitHub API + third-party
reviewer apps + real code) and is deliberately last.

---

## 6. Per-module plan

Each module: **3–5 checks for v1**, a CLI command, a docs page, fixtures with known answers
including must-not-flag cases, and `--dry-run`. Extra ideas go to `docs/ROADMAP.md`.

### 6.1 `guard_bench`

* **Discovery:** Claude Code `permissions.{allow,ask,deny}` across user `~/.claude/settings.json`,
  project `.claude/settings.json`, `.claude/settings.local.json`, managed settings (Appendix A.1.5);
  `PreToolUse` hooks from the same settings; plus a generic adapter for any executable that reads
  tool-call JSON on stdin and prints a decision (the documented Claude Code hook contract is used
  as the reference shape).
* **Corpus:** versioned YAML (`categories`, `base_intent`, `expected: block|allow`, `disguises`).
  Categories: destructive file ops, destructive git ops, secret reads, network exfiltration,
  privilege changes, guard self-modification. Disguise classes: compound (`&&`,`;`,`||`), pipe,
  subshell/backtick, multi-line + comments, quoting/escaping, variable indirection, alternative tool
  for same effect, interpreter one-liner (`python -c`, `node -e`), encoded string, file-tool
  (Read/Write/Edit) instead of shell. **Benign controls** (`git status`, `grep rm notes.txt`) measure
  false blocks.
* **Execution:** payloads are **strings only**; the hook process receives constructed hook JSON on
  stdin with a timeout; hooks run with a temporary `HOME` where possible; the report warns that a
  user hook script may itself have side effects.
* **Checks:** `guard.expected_block_allowed` (critical), `guard.benign_blocked`,
  `guard.disguise_class_floor`, `guard.regression_vs_baseline`, `guard.intent_uncovered`
  (static: destructive category with no matching rule).
* **Output:** pass rate per category, every disguised payload that got through *with its exact
  variant string*, false blocks, agent version, and a hash of the guard config;
  `--compare prev.json` for regressions.
* **Must-not-flag fixtures:** benign controls, a correctly blocking config, a config whose only
  miss is in a category the corpus marks `expected: allow`.
* CLI: `jev-harness guard run [--project PATH] [--corpus FILE] [--compare FILE]`.

### 6.2 `gate_calibration`

* **Input:** JSONL/CSV gate log (`id`, `decision`, `confidence`, optional features) + labels.
* **Labeling helper:** `sheet` stratifies by confidence × decision into a CSV; `report` merges labels
  back and refuses to score rows without labels (counted, not silently dropped).
* **Metrics:** reliability table, ECE, Brier, and per threshold: miss rate (risky items let through),
  work skipped, and an **upper confidence bound** on the miss rate.
* **Recommendation:** lowest-cost threshold whose upper-bound miss rate ≤ user target (e.g. 1%), plus
  how many additional labels are needed when the sample cannot support it.
* **Drift:** two windows compared with a multiplicity-corrected test (D11).
* **Fixtures:** synthetic generators for well-calibrated / overconfident / underconfident gates with
  analytically known answers (e.g. a gate that is exactly calibrated by construction), so assertions
  are exact rather than approximate.
* **Checks:** `calibrate.ece_above_target`, `calibrate.overconfident_bucket`,
  `calibrate.miss_bound_breach`, `calibrate.insufficient_labels`, `calibrate.drift_detected`.
* **Must-not-flag fixtures:** well-calibrated gate, small-but-sufficient sample, stable two windows.
* CLI: `jev-harness calibrate sheet|report|drift <log>`.

### 6.3 `compaction_survival`

* **Discovery:** Claude Code: root/nested `CLAUDE.md`, `CLAUDE.local.md`, `@` imports (≤4 hops),
  `.claude/rules/*.md` (always-on and `paths:`-scoped), skills, subagents. Codex: `AGENTS.md` chain
  root→cwd with `AGENTS.override.md`, fallback filenames, and the 32 KiB combined limit.
  The report records which sources exist and **how each one loads**.
* **Canaries:** a unique codeword per source (recall probe) and, for a few sources, a mechanically
  checkable behavioral rule. Planted only in a disposable copy.
* **Protocol per trial:** headless start in the copy → load every source → baseline probe →
  trigger compaction → probe → repeat for `--rounds` → repeat for `--trials` (default 5).
* **M4.0 spike (blocking, before implementation):** determine the *supported* compaction trigger.
  Candidates to test and document in `docs/AGENT_CLIS.md`: (a) `-p "/compact"`, (b) a tiny
  auto-compact window via `CLAUDE_CODE_AUTO_COMPACT_WINDOW` + `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`,
  (c) `--autocompact` (v2.1.221+), (d) detecting rounds from `PreCompact`/`PostCompact` hooks
  (Appendix A.1.3–A.1.4). Codex has `model_auto_compact_token_limit` (Appendix A.2.4), which looks
  cheap and deterministic. **If no reliable trigger exists, I stop and ask (OQ-3) rather than
  simulate and call it real.**
* **Modes:** `real` and `simulated` (pluggable summarizer). Reports state the mode and never mix them.
* **Checks:** `compaction.source_lost`, `compaction.progressive_decay`,
  `compaction.scoped_rule_lost`, `compaction.behavior_regression`, `compaction.source_not_loaded`
  (config sanity).
* **Must-not-flag fixtures:** simulated runs where a source survives all rounds; an import chain
  that is expected to survive.
* **Output:** survival matrix (source × round) per agent + version, Wilson intervals, and concrete
  fixes (promote to root file; re-inject a short pinned-rules file after compaction).
* CLI: `jev-harness compaction run [--agent claude-code|codex] [--rounds 3] [--trials 5] [--max-cost] [--mode real|simulated]`.

### 6.4 `reviewer_eval`

* **`mine`:** candidate fix commits from messages, linked issues, and small diffs touching source +
  tests. Printed for human confirmation; the judge may pre-rank, **never auto-accept**.
* **`build`:** for each confirmed fix, a branch that re-introduces the bug (revert the non-test part)
  with a neutral title/description, plus **control PRs** (harmless refactor, no bug) for false alarms.
* **Safety:** only `--target-repo`, only after a confirmation prompt listing every ref that will be
  pushed; never the source repo; the prompt warns that third-party reviewer apps will read the code.
* **`collect`:** polls the GitHub REST API for review comments with a timeout (token from
  `GITHUB_TOKEN` or `gh auth token`), and supports local reviewers via a command adapter
  (headless `claude`/`codex` review prompt).
* **Scoring:** a comment catches the bug when it points at re-introduced lines (location match within
  the changed hunk, via `unidiff`); the judge can additionally confirm the description; rules-only
  mode is location-only. Comments on control PRs or unrelated lines are noise.
* **Checks:** `reviewer.recall_below_floor`, `reviewer.noise_above_floor`,
  `reviewer.latency_exceeded`, `reviewer.insufficient_trials`, `reviewer.bug_hunk_untouched`
  (diagnostic).
* **Must-not-flag fixtures:** control PR with no comments (no noise), reviewer that finds the planted
  bug with one precise comment.
* **`cleanup`:** closes PRs and deletes branches unless `--keep`.
* CLI: `jev-harness reviewers mine|build|collect|report|cleanup`.

### 6.5 Uniform CLI

```
jev-harness guard run
jev-harness calibrate sheet|report|drift <log>
jev-harness compaction run
jev-harness reviewers mine|build|collect|report|cleanup
jev-harness report <result.json> --format md|json|github
```
Common flags: `--judge none|jev|anthropic|openai`, `--config`, `--output`, `--dry-run`,
`--max-cost`, `--max-runs`, `--fail-on flag|escalate`.

---

## 7. Dependency justification (brief §3 + §8.9)

| Dep | Why | Notes |
|---|---|---|
| `pydantic` v2 | Schemas + strict validation of judge output | required |
| `typer` + rich (typer's dep) | CLI | required |
| `httpx` | async HTTP (judges, GitHub, TypeSafe) | required |
| `pyyaml` | versioned payload corpus, `jev-harness.yml` | required |
| `unidiff` | hunk/line location matching in `reviewer_eval` | required, brief-listed |
| `bashlex` | shell AST for disguise analysis | D5; fallback tokenizer keeps us honest |
| `numpy`, `scipy` | Clopper–Pearson (`beta.ppf`), drift tests, reliability tables | **extra `[stats]`** only |
| `anthropic`, `openai` | optional judge adapters | extras `[anthropic]`, `[openai]` |
| `pytest`, `pytest-asyncio`, `syrupy`, `ruff`, `mypy`, `respx` | dev | `respx` for httpx mocking; a new dep — justified because it is the standard httpx mock and prevents live network in unit tests |
| `pydantic-settings` | env + YAML config | **only if** hand-rolled loading proves noisy; decide in M0 and record here either way |

Not added without your sign-off: any CLI wrapper lib beyond Typer, any stats/tabulate library
(Markdown tables are ~20 lines), any GitHub SDK (`httpx` + REST is enough and keeps auth behavior
explicit).

---

## 8. Safety and cost guardrails (brief §2.1, §2.2, §2.6, §2.9, §8.10)

* Never edit the user's real instruction files, hooks, settings, or main branch — all work happens in
  a `Workspace`, and the module detects and refuses the real working tree.
* Never execute a payload string in a shell. Guard payloads reach processes only as stdin JSON to a
  guard/hook that the user configured.
* No network in unit tests: `addopts = -m "not live"`, `respx` for httpx, recorded agent runs replayed.
* Live tests: `@pytest.mark.live`, skipped by default, documented in `CLAUDE.md`.
* Any live/paid path prints an estimate and supports `--dry-run`; `Budget` enforces `--max-cost` and
  `--max-runs` and aborts with exit code 2.
* Secrets: never logged, never in reports, `.env` gitignored, `.env.example` provided, API keys read
  from env only. Report headers redact anything matching key-like patterns.

---

## 9. Evidence and honesty policy (brief §2.7, §2.8)

* Every rate ships with `n` and a Wilson interval; below the configured minimum the finding is
  downgraded to a `too_few_samples` warning and the report says the sample cannot support a
  conclusion.
* Reports always print agent name **and version**, plus a hash of the guard/instruction config.
* Emulated matcher output is labelled as an approximation everywhere it appears.
* Simulated-compaction results are labelled and separated from real ones.
* README and docs contain no benchmark claims that a run in this repo did not produce. Where a claim
  cannot be made yet, the docs say "not measured yet".

---

## 10. Open questions for you (with my default if you don't answer)

* **OQ-1 (approval).** Approve this plan as written? Default: yes, proceed to M0.
* **OQ-2 (contextleleo).** I now have its real CLI (Appendix A.4). Confirm the importer should shell
  out to `contextleleo export <session-id>` and parse the "Simple" JSON document — and tell me whether
  `contextleleo list` has a machine-readable mode; if it does not, I will list ids by parsing its text
  output and mark that as fragile, or accept ids from a file. Default: `export` for content, ids from
  a file/flag, no scraping.
* **OQ-3 (compaction trigger).** The docs do not confirm `/compact` works as a `-p` prompt
  (Appendix A.1.4). Do you know a supported way to force compaction headlessly for the Claude Code
  version you run? Default: run M4.0 spike, implement simulated mode + whatever the spike proves, and
  stop before claiming real-mode numbers.
* **OQ-4 (Jev key + semantics).** Do you have a TypeSafe/Jev API key for live tests, and do you
  confirm the D3/D4 adapter semantics (single `choice` question per decision; confidence from the
  `choice` answer; retries on 429/529; failure ⇒ escalate)? Default: build to the documented schema,
  keep all Jev tests mocked/live-marked, and use `NullJudge` otherwise.
* **OQ-5 (Codex hooks in v1).** D7 defers them. Say the word and I will include a Codex `PreToolUse`
  hook adapter in v1 behind `--hooks codex`, with its payload contract documented as
  version-verified rather than guaranteed.
* **OQ-6 (real inputs for M5).** `reviewer_eval` needs a sandbox repo you own and are willing to push
  branches to, plus knowledge of which reviewer apps are installed on it. Nothing in M5 can produce a
  real number without that. Default: build M5 fully against recorded GitHub API fixtures and stop
  before the first live push, waiting for the sandbox repo name.
* **OQ-7 (extras naming).** Keep `[anthropic] [openai] [stats] [all]` exactly as in brief §3? Default: yes.

---

## 11. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Compaction may not be triggerable headlessly** (OQ-3). | M4.0 spike first; simulated mode is a legitimate, clearly-labelled path; stop-and-ask rather than fabricate. |
| R2 | Agent CLIs change flags/output; we rely on undocumented internals (session file paths, rollout JSONL). | Read `--help` and docs at build time, record every flag we depend on in `docs/AGENT_CLIS.md`, keep parsing tolerant, and fail loudly (never silently score zero). |
| R3 | Third-party reviewer apps may not exist / not run on a sandbox repo, and the judge/API may be slow. | Poll with timeouts, report "no review collected" as missing data (not as a miss), and support local command reviewers as a first-class alternative. |
| R4 | `bashlex` parse failures on real-world shell. | Fallback tokenizer path is always available; generator is template-driven; tests cover both paths; unsupported constructs are reported, not mistranslated. |
| R5 | Agent non-determinism makes results noisy. | Repeats + Wilson intervals + `too_few_samples` warnings; never report a point estimate without `n`. |
| R6 | Extra deps creep. | §7 list is closed; any addition is justified and recorded before use. |
| R7 | Real setup damage (the one thing that must not happen). | Disposable workspace only, refuse real working tree, guaranteed cleanup, no pushes outside `--target-repo` with confirmation. |
| R8 | Judge/API cost blowups. | Budget caps enforced in core, `--dry-run` everywhere, exit 2 on budget exhaustion. |

---

## 12. Out of scope for v1 (→ `docs/ROADMAP.md`)

Live permission verification against the real agent; Codex permission/sandbox-policy analysis;
multi-agent matrices beyond the two agents; reviewer cost accounting per PR comment; a hosted
dashboard; CI-level caching of runs; contextleleo round-trip writing (import only in v1).

---

## 13. As-built notes

What changed against this plan, and why. Updated at each milestone.

### M0 — scaffold, core, CI (done)

- **Added `core/errors.py`** (exit codes, `HarnessError`, `ConfigError`, `BudgetExceededError`) beyond the layout in brief §4. One base exception that the CLI maps to exit code 2 is easier to keep correct than scattering error types across modules.
- **`report --fail-on` is opt-in.** Rendering a stored result is not a judgement about it, so `report` exits 0 unless `--fail-on` is passed; module commands use the configured `fail_on` (default `flag`).
- **Markdown is timestamp-free by design** so it is snapshot-stable, and the generation time appears only in JSON. GitHub annotations are emitted separately from the step summary, because workflow commands must not land in the summary file (a test asserts this).
- **Wilson, Newcombe, the pooled score test and Holm are pure standard library.** Verified on a clean wheel install with no extras: Wilson works and Clopper-Pearson fails safely with an actionable message. So `[stats]` now buys exact intervals only, less than §7 originally implied.
- **Bounds analytically equal to 0 or 1 are snapped to the bound**: naive Wilson returned a lower bound of 1.4e-17 for 0 of 30 successes, which would have rendered as a precision claim.
- **`all` repeats its members** instead of self-referencing, because a self-referential extra is not build-safe. Dev tooling moved to `[dependency-groups] dev`, so `uv sync` installs it without `--all-extras`.
- **The GitHub annotation helper is `workflow_annotations`**: `from __future__ import annotations` binds the name `annotations` in every module, and mypy flagged the shadowing as a redefinition.
- **Version stays 0.1.0** until M6 bumps it to `1.0.0` per the release checklist.

M0 gate evidence (observed locally): 103 tests pass on Python 3.11.15 and on a separate 3.12 venv; `ruff check`, `ruff format --check` and `mypy` clean (8 source files, strict on `core`); `uv build` produces wheel + sdist; the wheel installs into a clean venv with core dependencies only (no scipy) and `jev-harness --version`, `config show`, `report --format md|json|github` all run there, with exit codes 0/1/2 as specified.

Toolchain used: uv 0.12.2, pytest 9.1.1, ruff 0.17.0, mypy 2.4.0, pydantic 2.14.1, typer 0.27.3.

### M1 — core: trace, importers, workspace, runner, judge, policy (done)

- **Added `core/importers/base.py`** (not in brief §4) so the three importers can share `SessionRef`, `TraceImporter` and `get_importer` without importing each other's modules. The registry lives here rather than in `importers/__init__.py` to keep the package import cycle-free.
- **`sessions list|show|export` CLI added.** Brief §7 does not name it, but the importer error message does (`run jev-harness sessions list --agent claude-code`), and inspecting a trace is how every later module gets debugged. They are read-only, so they do not carry `--judge`/`--max-cost` (same precedent as `config show`). `sessions list --root` requires `--agent`, otherwise a single root would silently be applied to both native importers.
- **contextleleo `list` refuses instead of scraping** human-readable output (D12). `export <id>` is shelled out to exactly as documented; the brief's assumed `export --format json <path>` does not exist.
- **Judge failure taxonomy:** remote failures (timeouts, HTTP, malformed bodies, low confidence, unmapped answers) become `escalated` Decisions — data, not crashes. A *missing* judge installation (`JudgeUnavailableError`, e.g. no `[anthropic]` extra) is re-raised as a configuration error (exit 2), because that is a broken install, not an uncertain verdict.
- **The judge can only add.** `PolicyEngine.apply_judge` takes the option→verdict `mapping` from the caller, so the judge cannot reinterpret its own question, and any rule that returned `escalate` keeps it regardless of what the judge says (invariant 3). Per-rule evidence lives in `RuleContext.evidence[rule_id]` so two rules cannot overwrite each other's.
- **Workspace path guard resolves symlinks before comparing.** Tests caught macOS `/var -> /private/var`, where a resolved temp path and its unresolved twin disagreed; the live-tree refusal now compares `resolve()`d paths.
- **Spike results are in `docs/AGENT_CLIS.md`**, not here: `claude` and `codex` are not installed on this machine, `~/.claude/settings.json` is absent, 2 real Claude Code transcripts exist (record shape observed, **no tool-call records in the sample**), `~/.codex` is absent. Runner argv is therefore *documented*, not *observed*; compaction triggering stays unverified (OQ-3) and `compaction run` must not claim a real number until a live run confirms a trigger.

M1 gate evidence (observed locally): 288 tests pass on Python 3.11; `ruff check`, `ruff format --check` and `mypy` clean (18 source files, strict on `core`). Sessions CLI exercised by hand against fixture roots: `list` (human + `--json`), `show` (summary + preview + `--json`), `export` (file + stdout), with unknown-agent / `--root` misuse / contextleleo-listing all exiting 2 with actionable messages. No live agent run happened (CLIs absent) — recorded as a limitation, not a pass.

---

### M2 — guard_bench (done)

- **Corpus is generated, not enumerated.** 29 base entries (21 dangerous, 8 benign controls) expand to 571 payloads: 503 expected-block and 68 benign. Disguise classes are template-driven (`variants.py`); no shell parser is needed to produce them (D5), so `bashlex` is used only by the *emulated permission matcher*, where dequoting and splitting top-level commands is the actual job. Nothing is executed.
- **Verdict model:** a payload is `stopped` (any block), `gated` (an `ask`), or `allowed`. `ask` counts as guarded because a person is prompted; hook crash, timeout, or any exit other than 0/2 counts as *got through*, mirroring the agent's non-blocking-error behaviour. An exit-0 hook with no decision has `no_opinion`, never `allow`.
- **Findings are per intent, not per payload.** `intent_uncovered` (plain form unguarded) and `expected_block_allowed` (plain form guarded, variants slipped) each carry every exact variant string in `evidence.variants` and their ids in `evidence.allowed_ids`; that is what `--compare` diffs, so a baseline needs only the saved report JSON.
- **Corpus digest ignores the project path** (found by a failing test: absolute-path variants made every checkout look like a different corpus). Different corpus ⇒ regressions are downgraded to `flag` with a caveat.
- **Hook environment is reduced.** Only `PATH`, locale/terminal vars, a temporary `HOME`, and `CLAUDE_PROJECT_DIR` are passed; `--pass-env` opts variables in. API keys are withheld by default.
- **`--judge` is accepted only as `none`** on `guard`; the module has no model path.
- **Fixture guards with known answers** live in `tests/fixtures/guard/hooks/`: a thorough guard that must produce no findings across all 503 dangerous and 68 benign payloads, a substring guard with known bypasses, an over-blocking guard, and crash/slow/ask/silent guards.
- **Not exercised:** a real `claude` run (CLI not installed here), so permission semantics rest on the documented rules and the emulator's stated assumptions.

## Appendix A — verified external interfaces

Everything below was read from vendor docs/source on 2026-10-13 and is the basis for D3–D8, D12.
Exact flags and payload shapes we end up depending on go into `docs/AGENT_CLIS.md` as we build.

### A.1 Claude Code

1. **Headless:** `-p`/`--print`; `--output-format text|json|stream-json`; `--input-format`;
   `--resume`/`-r`, `--continue`/`-c`, `--session-id <uuid>`; `--model`; `--max-turns`;
   `--add-dir`; `--verbose`; `--json-schema` (result in `structured_output`);
   `--dangerously-skip-permissions` (= `--permission-mode bypassPermissions`), `--allowedTools`,
   `--disallowedTools` (scoped form `Bash(rm *)`). Exit 0 on success, non-zero on failure.
   JSON output includes `total_cost_usd` (client-side estimate) and a per-model breakdown.
   — https://code.claude.com/docs/en/headless, https://code.claude.com/docs/en/cli-reference
2. **Hooks:** stdin JSON for `PreToolUse` is
   `{"session_id", "cwd", "hook_event_name", "tool_name", "tool_input"}`; the structured decision is
   `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow|deny|ask", "permissionDecisionReason": "..."}}`;
   exit code `0` alone is **not** approval, exit code `2` blocks (event-specific); `PostToolUse`/`Stop`
   use a top-level `decision: "block"`. Events include `SessionStart` (sources `startup|resume|clear|compact|fork`),
   `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`,
   `InstructionsLoaded`, `ConfigChange`, `SessionEnd`.
   — https://code.claude.com/docs/en/hooks-guide
3. **Compaction:** `/compact` summarizes; auto-compaction is configurable via `/autocompact`, the
   `--autocompact` flag (v2.1.221+), `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100k–1M, capped by model), and
   `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (can lower, not raise, the default percentage). `PreCompact` /
   `PostCompact` hooks exist for detection. `SessionStart` hooks with source `compact` run again.
   — https://code.claude.com/docs/en/context-window, https://code.claude.com/docs/en/env-vars
4. **Not verified:** that `/compact` is accepted as a `-p` prompt; the exact default threshold; an
   `autoCompactEnabled` setting; the full `PostToolUse` stdin schema.
5. **Instructions:** `~/.claude/CLAUDE.md`, project `./CLAUDE.md` or `./.claude/CLAUDE.md`,
   `./CLAUDE.local.md`, nested files on demand, `@` imports (≤4 hops), `.claude/rules/*.md` with
   optional `paths:` frontmatter, skills (`SKILL.md`), agents (`~/.claude/agents/`, `.claude/agents/`).
   Settings: `~/.claude/settings.json`, `.claude/settings.json`, `.claude/settings.local.json`,
   managed; `permissions.{allow,ask,deny}` evaluated deny → ask → allow; `Bash(ls:*)` equals trailing
   wildcard; a broad deny is not carved out by a narrower allow.
   — https://code.claude.com/docs/en/memory, https://code.claude.com/docs/en/permissions
6. **What compaction preserves** (important for our hypotheses): system prompt/output style, project-root
   `CLAUDE.md`, unscoped rules, auto memory are **re-injected**; path-scoped rules and nested `CLAUDE.md`
   are reloaded on demand; up to five recently modified files are re-read (files >5k tokens by path);
   invoked skill bodies are re-injected with token caps; earlier hook-added context is summarized.
   — https://code.claude.com/docs/en/context-window

### A.2 Codex CLI

1. **Headless:** `codex exec "<task>"` (Stable); `--json` emits a JSONL event stream
   (`thread.started`, `turn.started`, `item.started|completed`, `turn.completed` with
   `usage.{input_tokens,cached_input_tokens,output_tokens}`, `turn.failed`, `error`);
   `-o/--output-last-message`; `--output-schema`; `--cd/-C`; `-m/--model`;
   `--sandbox read-only|workspace-write|danger-full-access`; `--ask-for-approval never`;
   `--dangerously-bypass-approvals-and-sandbox` (alias `--yolo`); `--skip-git-repo-check`;
   `--ephemeral`; `--full-auto` is documented as deprecated.
   — https://developers.openai.com/codex/non-interactive-mode, https://developers.openai.com/codex/developer-commands
2. **Resume:** `codex exec resume <SESSION_ID>`, `--last`, `--all`. Sessions persist as rollout JSONL
   under `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` — repository implementation evidence, **not**
   a promised stable storage API.
3. **Instructions:** `$CODEX_HOME/AGENTS.override.md` else `AGENTS.md`; then project-root→cwd checking
   `AGENTS.override.md` → `AGENTS.md` → fallback filenames, concatenated root-to-leaf, default combined
   limit 32 KiB (`project_doc_max_bytes`); config also has `project_doc_fallback_filenames`,
   `developer_instructions`, `model_instructions_file`.
   — https://developers.openai.com/codex/agent-configuration/agents-md
4. **Compaction config:** `model_context_window`, `model_auto_compact_token_limit`,
   `model_auto_compact_token_limit_scope`, `compact_prompt`, experimental `experimental_compact_prompt_file`;
   hooks include `PreToolUse`/`PostToolUse`/`PreCompact`/`PostCompact` (`trigger` = `auto|manual`) via
   `hooks.json` or `[hooks]` with `features.hooks`. Hooks are documented but carry **no explicit Stable
   label**, require trust for non-managed hooks, and some paths opt out — hence D7.
   — https://developers.openai.com/codex/config-reference, https://developers.openai.com/codex/hooks

### A.3 TypeSafe / Jev (Judge adapter contract)

`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <API_KEY>`, body
`{state, model, questions}` where `model` is `jev-latest` (currently resolves to `jev-1.13.0`) and each
question is one of `noul` (yes/no probability), `choice` (`criteria` map, answer returns
`choice` + `probabilities` + `confidence`), `score` (`criteria` level array, returns weighted `score`
+ `legend` + `probabilities` + `confidence`). Response: `{model, answers: {<id>: Answer}, usage}`.
**Confidence exists only on `choice`/`score` answers** (this drove D3). Errors: 401 unauthorized,
422 validation, 429 rate limit, 529 overloaded — retry with backoff on 429/529.
Overrides used by the author's tooling: `JEV_API_URL`, `JEV_MODEL`, `JEV_API_KEY`/`TYPESAFE_API_KEY`.
— https://docs.typesafe.ai/api, https://docs.typesafe.ai/models

### A.4 contextleleo (optional trace importer)

Rust CLI plus library; harnesses include `claude_code`, `codex`, and others. Relevant commands:

* `contextleleo list [--from <harness>] [--cwd <dir>] [-n N] [--since] [--until]` — discover sessions.
* `contextleleo export <id>[#range] [--from <harness>] [--out <file>]` — writes the session as a
  **Simple JSON document** (to stdout or `--out`). Ids are unambiguous prefixes or exact titles.
* `contextleleo view <id>[#range] [--reveal] [--no-pager]` — compact text projection.
* `contextleleo query '<pattern>'` — search; `contextleleo context '<query>'` — Jev-ranked retrieval.
* The canonical model is `{meta, messages}` with typed blocks (`text`, `thinking`, `tool_use`,
  `tool_result`, `image`) — which maps cleanly onto our `Trace`/`Turn`.
* There is **no documented `--json` flag on `list`**, and export takes a session *id*, not a path.
  — https://github.com/Adityakk9031/contextleleo/blob/main/docs/usage.md (OQ-2)

### A.5 jev-build-sentinel (reference for the Jev client shape)

TypeScript GitHub Action choosing SKIP/TARGETED/FULL from Jev, with deterministic escalation when
confidence is low or risk high — the same "rules have the final say" pattern as our `PolicyEngine`.
Its client sets the retryable statuses (408/429/500/502/503/504/529), a 10s default timeout, strict
response validation, and degrade-to-FULL on failure.
— https://github.com/Adityakk9031/jev-build-sentinel
