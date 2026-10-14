# jev-harness

Test your AI coding-agent setup the way you test your code.

You configure coding agents (Claude Code, Codex, Cursor) with instruction files, hooks, permission
rules, AI code reviewers, and cheap model "gates". Almost nobody tests that configuration.
`jev-harness` answers four questions about it:

| Command | Question it answers | Needs a model? | Docs |
| --- | --- | --- | --- |
| `guard` | Do my hooks and permission rules block *disguised* dangerous commands? | no; optional `--judge` adds a model's opinion | [GUARD](docs/GUARD.md) |
| `calibrate` | When my gate says 70% sure, is it right 70% of the time, and what threshold is safe? | no; `calibrate gate` can run Jev as the gate | [CALIBRATE](docs/CALIBRATE.md) |
| `compaction` | Which of my instructions does the agent still know after it compacts its context? | simulated: no. real: the agent under test | [COMPACTION](docs/COMPACTION.md) |
| `reviewers` | Which AI code reviewer catches real bugs in *my* code, and how noisy is it? | the reviewers under test; optional `--judge` ranks candidates and confirms catches | [REVIEWERS](docs/REVIEWERS.md) |

## Install

`jev-harness` is not on PyPI yet. Install it from a checkout or from your Git remote:

```bash
uv tool install ./                                          # from this checkout
uv tool install 'jev-harness[stats] @ git+https://github.com/<you>/jev-harness'   # once pushed
```

`[stats]` adds scipy for exact Clopper-Pearson bounds in `calibrate`. Python 3.11 or newer. For
development: `uv sync --all-extras`, then prefix commands with `uv run`. Before testing against real
agents, GitHub, or reviewer apps, read [docs/REAL_TESTING.md](docs/REAL_TESTING.md).

Every command takes `--format md|json|github`, `--output PATH`, `--fail-on flag|escalate`, and
`--dry-run`. Exit code `0` is clean, `1` means a finding reached `--fail-on`, and `2` means the harness
could not run (bad input, missing credential, a budget was hit). Save a result with `--format json` and
render it later with `jev-harness report result.json --format md`.

## Five minutes per module

### guard: do my guards stop disguised commands?

Your guards already exist: `permissions` and `PreToolUse` hooks in `.claude/settings.json`. This sends
them dangerous commands in 11 disguises (`r''m`, `X=rm; $X`, base64, `python -c`, a file tool instead of a
shell, ...) plus harmless controls, and lists every exact variant that got through. Nothing is executed.

```bash
jev-harness guard run --dry-run          # what would be tested
jev-harness guard run                    # the report; exit 1 if anything got through
jev-harness guard run --format json -o before.json
jev-harness guard run --compare before.json    # after changing a hook or upgrading the agent
```

*Why it exists:* permission wildcards and hand-written hooks are easy to get subtly wrong, and each hook
tool tests only its own rules.

### calibrate: can I trust my gate's confidence?

Give it a log of gate decisions (`id`, `decision`, `confidence`) and a person's labels.

```bash
jev-harness calibrate sheet gate.jsonl --out sheet.csv --n 100          # label this sample
jev-harness calibrate report gate.jsonl --labels sheet.csv --target 0.01
jev-harness calibrate drift gate.jsonl --labels sheet.csv --split 2026-09-01T00:00:00Z
```

You get a reliability table, the calibration error, the miss rate at each threshold with an **upper
bound**, the cheapest threshold that stays under your target, and how many more labels you would need if
the data cannot yet say.

*Why it exists:* a confidence threshold is usually picked by feel, and no one measures how often the
"safe to skip" calls are wrong.

### compaction: what does my agent forget?

```bash
jev-harness compaction run --dry-run     # the instruction sources found and how each one loads
jev-harness compaction run               # simulated, free: compares summarizers on your files
jev-harness compaction run --mode real --max-runs 80 --max-cost 5 --rounds 2 --trials 3
```

It plants a unique codeword in a **copy** of each instruction source, compacts, and reports which
sources the agent still recalls, per round, with intervals. Simulated mode (the default) measures a
summarizer, is labelled as such, and is not evidence about any agent. Real mode needs a spend cap.

*Why it exists:* instructions that vanish after compaction fail silently, and which ones vanish depends on
where you put them.

### reviewers: which AI reviewer is worth paying for?

```bash
jev-harness reviewers mine --repo .                         # candidate fixes from your history
jev-harness reviewers build --repo . --target-repo you/sandbox --candidates candidates.json --dry-run
jev-harness reviewers build --repo . --target-repo you/sandbox --candidates candidates.json
jev-harness reviewers collect --reviewer 'coderabbitai[bot]' --wait 900
jev-harness reviewers report
jev-harness reviewers cleanup
```

It re-introduces bugs your team already fixed as pull requests on a **sandbox repository you name**, plus
bug-free control PRs, and scores each reviewer's recall, precision, noise, and latency by where its comments
land. **This pushes code to GitHub and every reviewer app on the sandbox will read it;** read
[REVIEWERS](docs/REVIEWERS.md) first.

*Why it exists:* reviewer benchmarks are run by vendors on open-source code, not on yours.

### In CI

`action/action.yml` is a composite GitHub Action for `guard` and `calibrate`:

```yaml
- uses: <you>/jev-harness/action@<tag>
  with:
    command: guard
    args: run --project .
    package: git+https://github.com/<you>/jev-harness@<tag>   # until it is on PyPI
    judge: jev                                  # optional
    jev-api-key: ${{ secrets.JEV_API_KEY }}     # required when judge is jev
```

## Using Jev as the judge

[TypeSafe Jev](https://typesafe.ai) is the judge this tool is built around. Four commands can use it, and
every one works without it.

```bash
export JEV_API_KEY=...                      # or: jev-harness --env-file ~/.jev.env ...
jev-harness judge check                     # one real call: does the key and endpoint work?
```

To use Jev wherever a command supports a judge, copy [jev-harness.example.yml](jev-harness.example.yml) to
`jev-harness.yml` (it sets `judge: jev`), or `export JEV_JUDGE=jev`. Otherwise pass `--judge jev` per command.

| Command | What Jev does | What stays rule-based |
| --- | --- | --- |
| `calibrate gate` | Is the gate: one choice per item, logged with the confidence it states, for `calibrate report` to test | the scoring against your labels |
| `guard run --judge jev` | Says whether each bypass, and each harmless control, is dangerous | whether a payload got through |
| `reviewers mine --judge jev` | Ranks candidate bug fixes | you confirm every fix |
| `reviewers report --judge jev` | Says whether a located comment describes the bug | whether the bug counts as caught |

* **A judge never overrides a rule.** It adds an opinion next to a result; the deterministic result stands.
* **A failed or unsure judge is "no opinion"** (below `min_judge_confidence`, default 0.7), never a
  confirmation or a pass.
* **Every judge command has a call cap** (`--max-runs`, default 200) checked before the first call, and
  `--dry-run` prints the number of calls. Judging sends payloads, diffs, and comments to the judge's service.
* **Anthropic and OpenAI judges** exist behind the same flag (`--judge anthropic|openai`, with the matching
  extra and key) but are unit-tested only. Jev is the one run live.

## Design promises

1. It never touches your real setup: everything runs on a disposable copy.
2. It never executes a dangerous payload: payloads reach a guard as JSON on stdin, never a shell.
3. It never pushes to a repository you did not name and confirm.
4. Deterministic rules have the final say; a model can add findings and opinions but never override an `escalate`, and
   a judge that fails or is unsure escalates instead of passing.
5. A rate measured from repeated trials ships with its sample size and a confidence interval, and a small
   sample is reported as provisional rather than as a result.
6. No number in these docs or in a report comes from anywhere but a run of this tool on your data.

## Honest limitations (1.1.0)

* **Several paths have not been run live.** No `claude` or `codex` CLI, GitHub sandbox repository, or real
  reviewer app was available while building this release. Agent behaviour is covered with scripted agents,
  GitHub with a mock plus a local bare repository, and the Action's script with a local `uvx` run. The first
  live run of `compaction --mode real` and of `reviewers build` is the first test of those paths; please
  report what you see.
* **`guard` approximates permission rules.** Hooks are executed for real; `permissions` are matched by an
  emulation (which understands quoting and top-level operators but not `$()`, `bash -c`, or interpreters)
  and the report says so. Confirm any permission bypass in the real agent.
* **`compaction` real mode relies on an unverified trigger prompt** (`/compact` by default), confirmed per
  measurement point by a `PreCompact` hook. If it cannot confirm a compaction it stops with exit 2 and
  reports no rate. Real mode supports Claude Code only; Codex sources work in simulated mode.
* **`reviewers` scores by comment location,** which shows a reviewer looked at the bug, not that it
  explained it. Each reviewer sees each bug once, so one run is one sample.
* **Judge opinions are advisory.** They are only as good as the model, they were checked live on a handful of
  items (not benchmarked), and a judge call sends your payloads, diffs, and comments to the judge's service.
* **Skills and subagents** are not probed by `compaction`; user-level instruction files are out of scope.
* A clean report means the payloads, labels, or bugs you gave it passed, not that your setup is safe.

## Development

See [CLAUDE.md](CLAUDE.md) for the commands, [docs/PLAN.md](docs/PLAN.md) for the plan, decisions, and
as-built notes, [docs/AGENT_CLIS.md](docs/AGENT_CLIS.md) for the agent flags this relies on and whether
each has been observed, and [docs/ROADMAP.md](docs/ROADMAP.md) for what was deferred.

```bash
uv sync --all-extras
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

MIT licensed.
