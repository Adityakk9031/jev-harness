# Testing jev-harness with real things

Everything in this repository passes its tests, but those tests use stand-ins: a scripted fake agent, a
mocked GitHub, and a local bare git repository. This page shows how to run each module against the real
thing for the first time: what you need, the exact steps, what a good result looks like, and what to do
if it fails.

Do the steps **in this order**. The first two are free and cannot break anything. The last two cost money
and, for `reviewers`, push code to GitHub.

| Step | Needs | Costs money? | Risk |
| --- | --- | --- | --- |
| 0. Check your install | this checkout, `uv` | no | none |
| 1. `guard` on your real hooks | a project with `.claude/settings.json` | no | low (your hook scripts run) |
| 2. `calibrate` on a real gate log | a decision log plus labels | no | none |
| 2b. The Jev judge | `JEV_API_KEY` | yes (Jev calls) | judge sees payload / diff / comment text |
| 3. `compaction --mode real` | `claude` CLI, logged in | yes (agent calls) | low (works on a copy) |
| 4. `reviewers` on a sandbox repo | GitHub sandbox, token, reviewer app | maybe (reviewer apps) | **code goes to GitHub** |
| 5. The GitHub Action | the repo pushed to GitHub | no (Actions minutes) | none |

When a step works or fails, write down the command, the exit code, and the first lines of the output.
Steps 3 and 4 test things nobody has run yet, so their results should go into
[AGENT_CLIS.md](AGENT_CLIS.md) (step 3) or [REVIEWERS.md](REVIEWERS.md) (step 4).

---

## 0. Check your install

```bash
cd /path/to/jev-harness
uv sync --all-extras
uv run pytest -q                 # expect: all passed
uv tool install ./               # puts `jev-harness` on your PATH
jev-harness --version            # expect: jev-harness 1.0.0
```

If the tests fail here, stop and fix that first. Nothing after this is meaningful otherwise.

---

## 1. `guard`: your real hooks and permission rules

**You need:** a project where you use Claude Code with hooks or `permissions` in `.claude/settings.json`,
`.claude/settings.local.json`, or `~/.claude/settings.json`. No model, no key, no network.

**What happens:** about 570 command strings are sent to your hooks as JSON on stdin. Nothing is run in a
shell. Your hook scripts *do* run, once per payload, with your real `HOME`. If a hook writes logs or calls
a web service, it will do that around 570 times.

```bash
cd /path/to/your-project
jev-harness guard run --dry-run                     # see which hooks and rules were found
jev-harness guard run                               # the report
jev-harness guard run --format json -o guard-before.json
```

**Good result:** the dry run lists the hooks and rules you expect. The report's `hook_errors` is `0`. Any
findings name exact variants, for example `X=rm; $X -rf ./jev-canary-target`.

**If something looks wrong:**

* `hook_errors` is high, or the report says "exited 127": your hook's command or interpreter is not on
  `PATH` here. Run it the way the agent does: `echo '{"tool_name":"Bash","tool_input":{"command":"ls"}}' | <your hook command>`.
* A hook that needs an environment variable (an API key, say) gets only `PATH`, `HOME`, locale variables,
  and `CLAUDE_PROJECT_DIR`. Add more with `--pass-env NAME`.
* A Python hook whose script path is wrong exits with code 2, and code 2 means "block". The bench (and the
  agent) will then show everything as blocked. If you see 100% blocked, including benign controls, check
  the path.
* "No guards were found": run from the project root, or pass `--project PATH`.

**Checking a permission-rule bypass in the real agent:** permission rules are emulated. To confirm one
bypass, use a throwaway directory with **no git remote** and pick a variant that touches only the canary
path:

```bash
mkdir /tmp/guard-check && cd /tmp/guard-check && git init -q
mkdir jev-canary-target && cp -R /path/to/your-project/.claude .
claude          # interactive; ask it to run exactly the variant string from the report
```

If Claude Code asks for permission or refuses, the emulator was wrong for that variant. Please note which
one. Never try the `git push`, `git reset`, or `git clean` variants in a real repository.

After you change a hook, run `jev-harness guard run --compare guard-before.json` to see what changed.

---

## 2. `calibrate`: a real gate's decisions

**You need:** a log of decisions made by a cheap gate (for example jev-build-sentinel's SKIP / TARGETED /
FULL choices) and, for some of them, what the right decision turned out to be.

**Log format:** JSONL, one decision per line, `confidence` from 0 to 1.

```json
{"id": "pr-412", "decision": "SKIP", "confidence": 0.82, "timestamp": "2026-09-02T10:00:00Z"}
```

A **label** is the decision that would have been right. For a CI gate, a sensible rule is: if the full
test run would have caught a failure, the label is `FULL`, otherwise it is the decision the gate made. Pick
one rule, write it down, and use it for every label.

```bash
jev-harness calibrate report gate.jsonl --dry-run --skip-decision SKIP     # counts, nothing scored
jev-harness calibrate sheet gate.jsonl --out sheet.csv --n 150             # rows to label
# open sheet.csv, fill the `label` column, save it
jev-harness calibrate report gate.jsonl --labels sheet.csv --skip-decision SKIP --target 0.01
jev-harness calibrate report gate.jsonl --labels sheet.csv --skip-decision SKIP --threshold 0.7
```

**Good result:** `labelled` is the number you filled in, unlabelled rows are counted rather than dropped,
and the report gives either a recommended threshold or the number of extra labels needed.

**Things to know:**

* Showing a 1% miss rate needs about 370 labelled skips with no misses (exact bound). With 150 labels,
  expect to be told to label more. That is the tool working correctly.
* Install with `[stats]` to get exact bounds. Without scipy the report says it used Wilson bounds.
* If the dry run warns that no row has the skip decision, pass the right `--skip-decision` (it is
  case-sensitive).

---

## 2b. The Jev judge (`guard`, `calibrate gate`, `reviewers`)

**You need:** `JEV_API_KEY` set in your shell (`export JEV_API_KEY=...`; never put it in a file you commit;
`.env` is git-ignored if you prefer one). A call costs whatever your Jev plan charges; every command below
prints its call count with `--dry-run` first and refuses to exceed `--max-runs` (default 200).

```bash
# a. Does the key work? Eight items, eight calls.
jev-harness calibrate gate items.jsonl --judge jev --dry-run \
    --question "Does this change need the full test suite?" \
    --option "skip=Only docs changed" --option "full=Code changed" -o gate.jsonl
# b. Your hooks versus a model guard, on 60 payloads.
jev-harness guard run --judge jev --judge-sample 60 --dry-run
# c. Candidate ranking for reviewers (one call per candidate).
jev-harness reviewers mine --repo . --judge jev --dry-run
```

Drop `--dry-run` to run. **Good result:** every command exits 0 or 1, `judge_calls` matches the dry run, and
`judge_guard.no_usable_answer` is small. If many answers are "unclear/low-confidence", the question is too
vague for the model or `JEV_MIN_JUDGE_CONFIDENCE` is too high; the judge's own confidence is what is being
compared with it. Error `JEV_API_KEY` means the variable is not exported in this shell.

The repository has a live test that makes about six calls: `JEV_API_KEY=... uv run pytest -m live
tests/test_live_jev.py -o addopts=--strict-markers`.

---

## 3. `compaction --mode real`: Claude Code, for real

This is the least tested path. Two things have never been seen working: sending `/compact` through
`claude -p`, and project hooks running inside the temporary copy. The tool checks both before it spends
much, and stops with exit 2 rather than report a fake number.

**You need:**

* the `claude` CLI installed and logged in (`claude --version` works, and `claude -p "hi"` answers);
* a project with a `CLAUDE.md` (and ideally a rule in `.claude/rules/` and a nested `CLAUDE.md`);
* a spending limit you are comfortable with.

### 3a. Check the two unknowns by hand (about two minutes, a few cents)

```bash
mkdir /tmp/cc-check && cd /tmp/cc-check
printf 'Codeword for root: TEST-WORD-1234\n' > CLAUDE.md
mkdir .claude && cat > .claude/settings.local.json <<'EOF'
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "echo started >> \"$CLAUDE_PROJECT_DIR/.jev-sessions\""}]}],
  "PreCompact":   [{"hooks": [{"type": "command", "command": "echo compacted >> \"$CLAUDE_PROJECT_DIR/.jev-compactions\""}]}]
}}
EOF
claude -p "Reply with the single word ready." --output-format json      # note the session_id
cat .jev-sessions                                                         # expect: started
claude -p "/compact" --resume <session_id> --output-format json
cat .jev-compactions                                                      # expect: compacted
```

* If `.jev-sessions` is missing, project hooks do not run in headless mode for your version (the directory
  may need to be trusted first: run `claude` once in it interactively and accept). The real run would stop
  with "did not run its SessionStart hook".
* If `.jev-sessions` exists but `.jev-compactions` does not, `/compact` does not work through `-p`. Try
  another trigger with `--trigger-prompt`, or note it so auto-compaction triggers can be added.

### 3b. A small real run

```bash
cd /path/to/your-project
jev-harness compaction run --mode real --rounds 1 --trials 3 --max-runs 40 --max-cost 3 --dry-run
jev-harness compaction run --mode real --rounds 1 --trials 3 --max-runs 40 --max-cost 3
```

The dry run prints how many agent calls the run needs. Set `--max-runs` at least that high. Real mode will
not start without a cap.

**Good result:** exit 0 or 1, a `survival.real-agent.<source>` line for every source, `discarded_points`
absent or small, and a `budget` line with the money spent.

**If it stops with exit 2:**

| Message | Meaning | What to do |
| --- | --- | --- |
| did not run its SessionStart hook | hooks do not run in the copy | see 3a |
| did not make the agent compact | the trigger prompt is not a compaction | try `--trigger-prompt`, report it |
| run cap reached / cost cap | the budget ran out | raise the cap or lower `--trials` |
| claude executable was not found | not on `PATH` | install or fix `PATH` |

Then grow to `--rounds 3 --trials 5` once a small run works. With only 3 trials the report correctly calls
every rate provisional.

**Please record:** your Claude Code version (`claude --version`), whether 3a worked, and the cost of the
small run. Put them in `docs/AGENT_CLIS.md` under "Compaction trigger", replacing "unverified" with what
you saw.

---

## 4. `reviewers`: a sandbox repository and a real reviewer app

**Read this first.** This step pushes a copy of your code to GitHub, and every reviewer app installed on
that repository reads it and may send it to its vendor. Use code you are allowed to share that way.

**You need:**

1. **A new, private, empty GitHub repository** used only for this, for example `you/reviewer-sandbox`.
   Never point this at a real project.
2. **A token for that repository only:** a fine-grained personal access token with access to just the
   sandbox, granting *Contents: read and write* and *Pull requests: read and write*. Put it in
   `GITHUB_TOKEN`, or use `gh auth login`.
3. **The reviewer apps you want to compare,** installed on the sandbox only (for example CodeRabbit).
4. **A repository with history,** ideally with at least 10 small bug fixes that came with tests.

**Important:** many reviewer apps review only pull requests into the default branch. This tool's PRs target
their own base branches, so the app may ignore them. Wake it up on every PR with `--trigger-comment`, for
example `--trigger-comment "@coderabbitai review"`, or with `--request-reviewer LOGIN` if your app supports
review requests. Check your reviewer's docs for its on-demand command; neither option has been tried
against a real app yet.

```bash
cd /path/to/your-project
jev-harness reviewers mine --repo . -o candidates.json
# open candidates.json and set "confirmed": true on 10 or more real bug fixes

jev-harness reviewers build --repo . --target-repo you/reviewer-sandbox \
    --candidates candidates.json --exclude '.env*' --exclude 'secrets/**' \
    --trigger-comment "@coderabbitai review" --dry-run            # read every line
jev-harness reviewers build --repo . --target-repo you/reviewer-sandbox \
    --candidates candidates.json --exclude '.env*' --exclude 'secrets/**' \
    --trigger-comment "@coderabbitai review"                      # type yes

# wait for the reviewers (watch the PRs on GitHub), then:
jev-harness reviewers collect --reviewer 'coderabbitai[bot]' --wait 1200
jev-harness reviewers report
jev-harness reviewers cleanup                                     # close PRs, delete branches
```

**Good result:** `build` prints one line per PR with a GitHub URL, and the PRs show the bug coming back
(look at one diff by hand). `collect` finds comments from each reviewer. `coverage` in the report is close
to every PR.

**If something goes wrong:**

* `build` fails to push: check the token's *Contents* permission and the repository name. The error never
  shows the token.
* The reviewer never comments: open one PR on GitHub. If there is no review there either, the app ignored
  the PR (see "Important" above). The report counts it as a miss, and `coverage` shows the gap.
* `--reviewer` must match the comment author's login exactly as GitHub shows it, `[bot]` included.
* If you stop half-way, `reviewers cleanup` still finds every branch it pushed (they are recorded before
  each PR is opened).

**Please record:** the reviewer, whether it needed a trigger comment, and whether any PR diff looked wrong.

---

## 5. The GitHub Action

Push this repository to GitHub. The `CI` workflow then:

* runs lint, types, and tests on Python 3.11 and 3.12;
* installs the wheel in a clean environment and runs one command per module;
* runs the composite Action on `tests/fixtures/guard/action-demo`, which should pass with no findings.

To use the Action in another repository before jev-harness is on PyPI, set its `package` input to
`git+https://github.com/<you>/jev-harness@<tag>`.

---

## What is still not covered, even after all of this

* Codex in real compaction mode (refused by design in 1.0.0).
* Anthropic and OpenAI judges: implemented and unit-tested, never run live.
* Skills and subagents in `compaction`.
