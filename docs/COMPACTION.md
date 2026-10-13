# compaction: which of my agent's instructions survive context compaction?

`jev-harness compaction run` plants a unique codeword (and, for a couple of sources, a mechanically
checkable rule) in a **disposable copy** of your instruction files, makes the agent compact its context,
and reports which sources it still knows afterwards, per source and per round, with intervals.

## Two modes, never mixed

| | `--mode simulated` (default) | `--mode real` |
| --- | --- | --- |
| What runs | A synthetic session and a summarizer you choose | The real `claude` CLI, headless |
| What it measures | The summarizer and the pipeline | The agent |
| Cost | none | paid; needs `--max-cost` and/or `--max-runs` |
| Network / keys | none | the agent's own |
| Report banner | **Simulated run** | none |

Simulated numbers describe the summarizer, not any agent, and must not be quoted as agent behaviour. The
built-in strategies are `keep-recent` (keeps the last 10 messages), `extractive` (the first 80 characters of
each message), and `claude-code-documented`, which is a **model of what Claude Code's documentation says**
(root `CLAUDE.md` and unscoped rules re-injected; path-scoped rules and nested files reloaded on demand;
sources the docs are silent about are treated as lost). Bring your own with
`--summarizer package.module:function` (a function from a list of strings to a summary string; it runs with
your privileges).

## Quickstart

```bash
uv run jev-harness compaction run --dry-run                       # sources found, plan, call counts
uv run jev-harness compaction run                                 # simulated, free
uv run jev-harness compaction run --strategy extractive --canary-position start
uv run jev-harness compaction run --mode real --max-runs 80 --max-cost 5 --rounds 2 --trials 3
```

Exit code `1` means a finding reached `--fail-on` (default `flag`); `2` means the run could not happen
(no instruction files, no cap in real mode, the compaction trigger did not work, a budget was hit).

## Sources discovered

Claude Code: root `CLAUDE.md` (or `.claude/CLAUDE.md`), `CLAUDE.local.md`, `@` imports (up to four hops),
unscoped `.claude/rules/*.md`, path-scoped rules (`paths:` frontmatter), and nested `CLAUDE.md` files.
Codex: `AGENTS.override.md` / `AGENTS.md` at the root and nested `AGENTS.md` files, plus a warning when the
combined size passes Codex's default 32 KiB limit. The report records **how each source loads**.
Skills and subagent definitions are listed as *not probed*, with the reason. User-level files are out of scope.

## How a real run measures

One **fresh session per measurement point** (trial, round): load the sources, compact `round` times, probe
once. A single long session would let the agent "remember" a codeword because it said it in an earlier
probe, which the summary can keep without the instruction file surviving.

1. Load: the agent is asked to read a trigger file for each path-scoped or nested source, so those load as
   they would in real work (the harness plants the trigger file in the copy).
2. Compact: the trigger prompt (`--trigger-prompt`, default `/compact`) is sent `round` times. **A
   `PreCompact` hook installed in the copy writes a marker, and a point whose compaction left no marker is
   discarded, never scored.** If no compaction is ever confirmed the run stops with exit 2. That is what
   makes an unverified trigger safe to try; see `docs/AGENT_CLIS.md`.
3. Recall probe: tools disabled, asked for every `Codeword for <name>` it still knows. Scored by exact
   codeword match.
4. Behavior probe (two sources by default): the agent creates a file; the rule says its first line must be an
   exact header. Scored mechanically from the file.

A real run makes `trials x sum over rounds r of (1 + r + 1 + behavior_sources)` agent calls, shown by
`--dry-run`. The cost is not estimated in dollars (prices change); the agent reports `total_cost_usd` per
call and the caps stop the run.

## Checks

| Rule id | Severity | Meaning |
| --- | --- | --- |
| `compaction.source_not_loaded` | flag | Not recalled even before compaction: a loading problem. |
| `compaction.source_lost` | flag | Recalled at baseline, credibly below the floor (default 80%) after compaction. |
| `compaction.scoped_rule_lost` | flag | The same, for path-scoped rules and nested files, which the docs say load on demand. |
| `compaction.progressive_decay` | flag | Recall falls round after round, significantly, without having collapsed yet. |
| `compaction.behavior_regression` | escalate | A rule followed at baseline stops being followed after compaction. |

"Credibly below" means the Wilson interval's **upper** end is below the floor, and a stochastic cell needs
at least three trials. One bad trial never condemns a source. Deterministic strategies have no sampling
noise, so they run one trial and a single failure is certain.

## What it does not prove

* Recall of a codeword is not the same as following an instruction well. A source that survives is not
  thereby obeyed.
* Real mode relies on a compaction trigger that is **unverified** until a run confirms it, and on the
  `PreCompact` hook being honoured. Only `claude-code` is supported in real mode; Codex has no verified way
  to confirm a compaction.
* A handful of trials gives wide intervals; the report says so.
* Your real files are never edited: planting happens in a temporary copy, which is deleted afterwards.
