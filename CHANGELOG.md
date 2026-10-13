# Changelog

All notable changes are recorded here. Rule ids (`guard.*`, `calibrate.*`, `compaction.*`,
`reviewer.*`) are public identifiers that `--compare` and the GitHub Action depend on; renaming one
is listed under *Changed*.

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
