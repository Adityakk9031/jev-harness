# Changelog

All notable changes are recorded here. Rule ids (`guard.*`, `calibrate.*`, `compaction.*`,
`reviewer.*`) are public identifiers that `--compare` and the GitHub Action depend on; renaming one
is listed under *Changed*.

## 1.2.0

First public release on PyPI, as **`jev-harness-sdk`** (the name `jev-harness` belongs to an unrelated
project). The command is still `jev-harness`, and the Python package is still `jev_harness`.

Found while recording the end-to-end demo against a live sandbox repository with CodeRabbit and cubic.

### Changed

* Distribution renamed to `jev-harness-sdk`; install hints, the GitHub Action's default `package`, and
  the docs use the new name.
* CI type-checks and tests each matrix job on its own Python version.

### Added

* `--format summary`: a short terminal readout (headline numbers, the top findings with two examples
  each, and the banners), ending with a pointer to `--format md` for every limitation and all evidence.
* A `.env` file in the folder you run from is read automatically (only the variables this tool knows);
  `--env-file PATH` still wins, and the real environment still wins over either.
* `demo/`: a scripted demo kit (a guard target project, gate items, a bug-fix repo builder, a preflight
  check, the captured screenshots) and `demo/launch-video/`, the Remotion project for the launch video.

### Fixed

* `reviewers collect` counted reviewer-app status notices ("Review limit reached", "Currently
  processing") as reviews. They are now marked as status notices, never count as an answer, a catch,
  coverage, or latency, and are listed in the report so a missing review is explained.
* `reviewers` latency used a bot's instant "Action performed" acknowledgement as its first comment; it now
  uses the first review content.
* The Jev judge read reviewer comments with their HTML markup (`<details>`, comments, tags); it now
  reads the readable text.
* `reviewers cleanup` reported the sandbox's default branch as "already gone" when GitHub refused to
  delete it; it now keeps that branch and says why, and raises any other refusal instead of reporting it
  as done.
* Compaction messages read "was followed recalled"; they now say "was followed at baseline but not
  followed".

## 1.1.1

Found by running the Jev integration at scale against the live endpoint (see `docs/LIVE_RESULTS.md`).

### Fixed

* `calibrate gate` logged Jev's `confidence` statistic as if it were a probability, which made a
  well-calibrated judge look badly underconfident. It now logs the probability of the chosen option
  (`Decision.probability`) and keeps Jev's statistic as `judge_confidence`; `--score confidence` restores the
  old behaviour.
* The `guard --judge` question told the judge that scratch files were exempt, so it called 27 canary-file
  bypasses safe; the wording no longer says so, names the agent's own configuration files, and describes
  what "safe" and "unclear" mean.

### Added

* `calibrate gate --hide FIELD`, `--score probability|confidence`.
* `docs/LIVE_RESULTS.md`, held-out guard corpora, a labelled set of reviewer comments, and a seeded gate
  data generator under `tests/fixtures/live/`.

### Changed

* Default judge concurrency raised from 4 to 8 (80 calls at 32-way concurrency ran with no failures).

## 1.1.0

Model judges are now used where they help. They are advisory and off by default.

### Added

* `calibrate gate`: run a judge (for example Jev) as the gate over a JSONL of items, producing a gate log
  with the judge's answers and stated confidences for `calibrate report` to score.
* `guard run --judge`: asks a judge whether payloads that got through, and benign controls, are dangerous.
  Adds `judge_guard.*` metrics and a per-variant `judge` opinion; never changes what counts as a bypass.
* `reviewers mine --judge`: ranks candidate fixes (never confirms them).
* `reviewers report --judge`: asks whether located comments describe the bug; adds `confirmed_recall` and the
  judge-sourced finding `reviewer.catch_not_confirmed`. Answers are cached in the manifest.
* A shared call budget (`--max-runs`, default 200) checked before any judge call, and `--dry-run` call counts.
* `tests/test_live_jev.py` (opt-in) and a Jev section in `docs/REAL_TESTING.md`.
* `jev-harness judge check`: one real call that shows whether a key and endpoint work.
* `--env-file PATH` (global): read `KEY=VALUE` settings from a file you name. Only known variables are read,
  the real environment wins, and values are never printed.
* `jev-harness.example.yml`: a config that makes Jev the default judge.
* The GitHub Action gained `judge` (`none` or `jev`) and `jev-api-key` inputs; the key is dropped from the
  environment when no judge is requested.

### Changed

* `--judge` is accepted by `guard`, `calibrate gate`, `reviewers mine` and `reviewers report`. The other
  commands (which use no model) still accept only `none`.
* The reviewers module now has six checks (`reviewer.catch_not_confirmed` is produced only with a judge).

## 1.0.0

First release. A shared core and four modules for testing an AI coding-agent setup.

### Added

* **Core:** trace schema with importers for Claude Code sessions, Codex rollout files and contextleleo;
  disposable workspaces that refuse the real working tree; agent runners for `claude -p` and
  `codex exec` with recording and replay; pluggable judges (Jev, Anthropic, OpenAI, none) that fail
  safe to `escalate`; a policy engine in which rules outrank any judge; Wilson and Clopper-Pearson
  intervals with sample-size warnings; JSON, Markdown and GitHub step-summary reports.
* **`guard`:** a versioned corpus of dangerous commands with disguised variants and benign controls,
  run against `PreToolUse` hooks and an emulated permission matcher. Checks: `guard.intent_uncovered`,
  `guard.expected_block_allowed`, `guard.benign_blocked`, `guard.disguise_class_floor`,
  `guard.regression_vs_baseline`.
* **`calibrate`:** `sheet`, `report` and `drift` for gate decision logs: reliability table,
  calibration error, per-threshold miss rate with an upper bound, threshold recommendation, labels
  still needed. Checks: `calibrate.ece_above_target`, `calibrate.overconfident_bucket`,
  `calibrate.miss_bound_breach`, `calibrate.insufficient_labels`, `calibrate.drift_detected`.
* **`compaction`:** planted codewords and behavior rules in a disposable copy of your instruction
  files; simulated mode (default) and a real mode for Claude Code that confirms every compaction with
  a `PreCompact` hook. Checks: `compaction.source_not_loaded`, `compaction.source_lost`,
  `compaction.scoped_rule_lost`, `compaction.progressive_decay`, `compaction.behavior_regression`.
* **`reviewers`:** `mine`, `build`, `collect`, `report`, `cleanup`: re-introduce fixed bugs from your
  history as pull requests on a sandbox repository and score AI reviewers by comment location. Checks:
  `reviewer.recall_below_floor`, `reviewer.noise_above_ceiling`, `reviewer.latency_exceeded`,
  `reviewer.insufficient_trials`, `reviewer.bug_hunk_untouched`.
* A composite GitHub Action (`action/action.yml`) for `guard` and `calibrate`.
* `docs/REAL_TESTING.md`: a step-by-step guide to the first runs against real agents, GitHub, and
  reviewer apps.

### Fixed before release

* Agent runners parsed only the last 2,000 characters of stdout, so Claude's single-line JSON envelope
  (and long Codex event streams) would have been unparseable on every real run.
* `guard` ran hooks with a temporary `HOME`, so hooks installed under `~/.claude` were "not found" and
  every payload looked like a bypass. Hooks now get the real `HOME`; `--isolate-home` restores the old
  behaviour, and exit code 127 is called out in the report.
* `compaction --mode real` now plants a `SessionStart` hook as well, and stops with a clear message when
  hooks do not run in the copy instead of blaming the compaction trigger.
* `reviewers build` gained `--trigger-comment` and `--request-reviewer`, because many reviewer apps skip
  pull requests that do not target the default branch.
* The install instructions and the Action's `package` input no longer assume a PyPI release.

### Known limitations

* No live `claude` or `codex` run, no live GitHub push, and no real reviewer app were exercised
  while building this release. The agent paths are covered with scripted agents and recorded shapes,
  the GitHub path with a mock plus a local bare repository. See each module's docs.
* Permission rules in `guard` are matched by an emulation and are labelled an approximation.
* The compaction trigger prompt for real mode is unverified; a run that cannot confirm a compaction
  stops with exit code 2 rather than reporting a survival rate.
* `--judge` is accepted only as `none` by the four modules in this release; the judge adapters are
  part of the core for later use.
