# reviewers: which AI code reviewer catches real bugs in my code?

Vendor benchmarks run on open-source code. `jev-harness reviewers` measures the reviewers you actually pay
for, on bugs your own team already fixed. It takes real fixes from your history, re-introduces each as a
pull request on a **sandbox repository you name**, adds control PRs that contain no bug, waits for your
reviewer apps (or runs local reviewer commands), and scores where their comments land.

```
mine -> (you confirm) -> build -> collect -> report -> cleanup
```

## Read this before you build

`reviewers build` **pushes code to GitHub**. It does so only to the repository you name with
`--target-repo`, and only after showing every branch it will push and asking you to type `yes` (or passing
`--yes`). It refuses a target that is a remote of the repository it is reading. It pushes a snapshot of the
tracked files at each fix commit with no history, and `--exclude GLOB` removes paths from every snapshot.
**Every reviewer app installed on the sandbox repository will read that code and may send it to a third
party.** Use a private sandbox repository, install only the reviewers you mean to test, and exclude secrets.

The token (`GITHUB_TOKEN`, or `gh auth token`) needs access to the sandbox only. It is never printed or
logged, and is passed to git through the environment rather than the command line.

## Quickstart

```bash
uv run jev-harness reviewers mine --repo . -o candidates.json     # ranked candidate fixes
# edit candidates.json: set "confirmed": true on the fixes you want
uv run jev-harness reviewers build --repo . --target-repo you/reviewer-sandbox \
    --candidates candidates.json --dry-run                         # see exactly what would be pushed
uv run jev-harness reviewers build --repo . --target-repo you/reviewer-sandbox \
    --candidates candidates.json                                   # confirm, push, open PRs
uv run jev-harness reviewers collect --reviewer coderabbitai[bot] --wait 900
uv run jev-harness reviewers collect --local-reviewer 'claude=./review.sh {diff}'
uv run jev-harness reviewers report
uv run jev-harness reviewers cleanup                               # close PRs, delete branches
```

State lives in one manifest (default `.jev-harness/reviewers/manifest.json`). Every command accepts
`--dry-run`; `--judge` is accepted only as `none` in this version.

## How each step works

* **mine:** ranks recent commits that touch source *and* tests, are small (`--max-lines`, default 40), skip
  reverts and root commits, and whose message mentions a fix or links an issue. It writes a list for a
  person to confirm. **Nothing is ever auto-accepted.**
* **build:** for each confirmed fix, a base branch holds the code *with* the fix and a head branch reverses
  the fix's non-test changes, so the pull request's diff is the bug coming back and the test that guards
  it is still there. A **control PR** (up to three by default, or `--controls N`) applies the same fix
  forward: a real change with nothing wrong in it. Titles and descriptions come from file names, never from
  the fix's commit message.
* **collect:** pulls inline comments, review bodies, and issue comments for every PR, excluding the token's
  own user. `--wait` polls until every `--reviewer` has answered every PR, nothing new arrives for
  `--settle` seconds, or time runs out. `--local-reviewer NAME=COMMAND` runs a command per PR with `{diff}`
  replaced by a file path; it should print a JSON array of `{path, line, body}` or `path:line: text` lines.
  The command runs with your privileges, without a shell.
* **report:** scores by location (below) and renders recall, precision, noise, latency, and coverage.
* **cleanup:** closes the PRs and deletes the branches recorded in the manifest, on the manifest's own
  repository, after confirmation. `--keep` leaves everything.

## Scoring

A comment **catches** a bug when it is an inline comment on the re-introduced lines of that PR (within
`--slack` lines, default 2; left-side comments are matched against the deleted lines). Everything inline that
is off the bug, or anywhere on a control PR, is **noise**. Summary comments carry no location, so they are
counted separately and are neither.

| Rule id | Severity | Meaning |
| --- | --- | --- |
| `reviewer.recall_below_floor` | flag | Even the upper end of the recall interval is below `--recall-floor` (default 50%); needs at least 3 bugs. |
| `reviewer.noise_above_ceiling` | flag | More than `--noise-ceiling` (default 3) noisy comments per PR. |
| `reviewer.latency_exceeded` | flag | Median time to a first comment above `--latency-ceiling` (default 900 s). |
| `reviewer.insufficient_trials` | flag | Fewer than `--min-prs` (default 10) planted bugs: every rate is provisional. |
| `reviewer.bug_hunk_untouched` | info | No reviewer commented on a planted bug; it may be too subtle to see from the diff. |

The floors and ceilings are defaults you should set for your team, not benchmarks.

## What it does not prove

* **Location is not understanding.** A comment on the right lines shows the reviewer looked there, not that it
  explained the bug. Confirming the description needs a judge, which is on the [roadmap](ROADMAP.md).
* **One run is one sample.** Reviewers are not deterministic and each saw each bug once.
* A reviewer that did not answer is counted as a miss; `coverage` shows how many PRs it answered at all.
* Snapshots carry no history, so a reviewer may have less context than on your real repository.
* Nothing in this module has been run against live GitHub or a real reviewer app in this repository's tests:
  the GitHub API is exercised through a mock, and pushes go to a local bare repository. The first live run
  should be treated as the first test of that path.
