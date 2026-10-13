# jev-harness

Test your AI coding-agent setup the way you test your code.

You configure coding agents with instruction files, hooks, permission rules, AI code reviewers, and
cheap model "gates". Almost nobody tests that configuration. `jev-harness` answers four questions
about it:

| Module | Question it answers | Needs a model? |
| --- | --- | --- |
| `guard_bench` | Do my hooks and permission rules block *disguised* dangerous commands? | no |
| `gate_calibration` | When my gate model says 70% confident, is it right 70% of the time? | no |
| `compaction_survival` | Which of my instructions does the agent forget after it compacts? | yes (agent under test) |
| `reviewer_eval` | Which AI reviewer catches real bugs in *my* code, at what noise cost? | yes (reviewers under test) |

**Status: v0.1.0 scaffold.** The core (trace schema, workspaces, agent runner, judge adapters,
policy engine, statistics, reporting) is being built first; the four modules follow. Nothing here is
released, and this README will not claim a benchmark number that a run in this repository did not
produce.

## Design promises

1. It never touches your real setup - everything runs on a disposable copy.
2. It never executes a dangerous payload - payloads reach a guard as stdin JSON, never a shell.
3. Deterministic rules have the final say; a model can add findings but never override an escalate.
4. `guard` and `calibrate` work with no model key at all.
5. Anything measured from repeated trials ships with its sample size and a confidence interval, and
   small samples are reported as provisional rather than as results.

## Development

See [CLAUDE.md](CLAUDE.md) for the exact commands, and [docs/PLAN.md](docs/PLAN.md) for the plan,
decisions, and known limitations.
