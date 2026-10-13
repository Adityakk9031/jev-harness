# Roadmap

v1 keeps each module to 3-5 checks (brief section 8.8). Everything below is deliberately deferred so
the released checks are the ones that are actually verified.

## Deferred from v1, with the reason

| Item | Why it is not in v1 |
| --- | --- |
| Live verification of permission rules against a real agent session | v1 ships static analysis plus an emulated matcher, labelled as an approximation. Running the real evaluator needs a per-version harness. |
| Codex hooks and permissions in `guard_bench` | Codex hooks are documented but carry no explicit *Stable* maturity label and need per-project trust, and Codex expresses safety as sandbox + approval policy rather than allow/deny rules. |
| Codex `AGENTS.md` override analysis beyond discovery | Discovery is in v1 (it feeds `compaction_survival`); interpretation of the 32 KiB combined limit as a failure mode needs a spike. |
| Writing sessions back through `contextleleo` (round trip) | v1 imports only. |
| A live-verification mode for the emulated permission matcher | Needs recorded real-agent decisions per agent version to diff against. |
| Multi-agent matrices (more than Claude Code and Codex) | Each new agent needs its own verified probe protocol. |
| Reviewer cost accounting per PR comment | Needs a price table the user owns; v1 reports latency only. |
| Hosted dashboard, run history, CI caching of agent runs | Out of scope for an SDK + CLI release. |
| `ContextleleoImporter` scraping `contextleleo list` output | Its `list` has no documented machine-readable mode, so v1 takes ids explicitly instead of parsing human output. |

## Ideas to evaluate later

* **Pinned-rules file generator.** When `compaction_survival` finds sources that die, emit a short
  file that is re-injected after compaction and re-measure. The fix already belongs in the report as
  advice; generating it is the natural next step.
* **Guard corpus from real history.** Derive disguised payloads from the commands a developer
  actually runs (via a trace importer) instead of a general corpus, so the bench measures the
  commands that matter in this repo.
* **Calibration-aware gate defaults.** Emit a ready-to-paste threshold config for the user's gate
  from `calibrate report`, including the upper-bound miss rate it implies.
* **Reviewer regression watch.** Run `reviewers report --compare` on a schedule so a reviewer app
  update that quietly stops catching bugs shows up as a regression.
* **Cross-module "setup health" summary.** One report that aggregates the four modules into a single
  verdict, once each module has a stable finding vocabulary.
