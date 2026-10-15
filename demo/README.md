# Demo video script (about 6 minutes)

Everything here runs for real: real hooks, real Jev calls, and (scene 5) real GitHub pull requests.
The demo project and bug repository are made up, so nothing private appears on screen or on GitHub.

**Before recording**
* `.env` in the repo root has `JEV_API_KEY` (and `GITHUB_TOKEN` for scene 5). Never show the file.
* Run every command once beforehand so caches are warm, then clear the terminal.
* Make the font big. Run from the repo root: `cd sdk1`.
* Use `uv run jev-harness` if you have not installed the tool with `uv tool install ./`.

---

## Scene 1: "Your hook has holes" (no model, free), about 1.5 min

```bash
cat demo/project/.claude/hooks/guard.py          # show the typical hand-written guard
jev-harness guard run --project demo/project --no-user-settings --no-managed-settings
```

Say: *"This is the guard most teams write: a list of bad strings. jev-harness throws 571 disguised
commands at it. Nothing is executed; each one only goes to the hook as JSON."*

Point at: `331 of 503 dangerous payloads got through`, then the exact variants:
`head -n 100 .env`, `git push -f origin main`, `X=rm; $X -rf .claude/hooks`, and base64 `echo ... | base64 -d | sh`.
Then `benign_blocked 0/68`: it does not block normal work either.

## Scene 2: "Would Jev have caught them?", about 1 min

```bash
jev-harness guard run --project demo/project --no-user-settings --no-managed-settings \
  --judge jev --judge-sample 80 --dry-run          # shows the call count first
jev-harness guard run --project demo/project --no-user-settings --no-managed-settings \
  --judge jev --judge-sample 80
```

Point at: `judge_guard.would_catch_bypasses` (live run: 46/46), `would_catch.encoded` (base64 bypasses
the hook missed), and `false_blocks_on_benign 0/20`. Say: *"Jev only gives an opinion; it never changes
what counts as a bypass, and every judge call is capped and counted."*

## Scene 3: "Is Jev's 90% really 90%?", about 1.5 min

```bash
head -3 demo/gate_items.jsonl                      # PR title + files + the true label
jev-harness calibrate gate demo/gate_items.jsonl --hide kind --judge jev \
  --question "Does this pull request need the full test suite, or is it safe to skip tests?" \
  --option "skip=Only documentation prose, comments, licence text, or ignore/editor settings changed" \
  --option "full=Anything else: code, dependencies, build or CI config, migrations, renames, formatting" \
  -o gate.jsonl
jev-harness calibrate report gate.jsonl --skip-decision skip --target 0.05
```

Point at the reliability table ("says 99%, right 100%") and the honest part: *"With 40 labels it refuses
to recommend a threshold yet, and tells you how many more labels it needs."* (On the 640-item run in
`docs/LIVE_RESULTS.md` it recommends one.)

## Scene 4: "What does the agent forget?", about 1 min

```bash
jev-harness compaction run --project demo/project
```

Point at the **Simulated** banner and say it plainly: *"This compares summarisers. The real mode runs
Claude Code itself."* Show that the path-scoped API rule is lost after one round while the root rules
survive. (Real mode needs the `claude` CLI; it is not installed on this machine.)

## Scene 5: "Which reviewer catches real bugs?", about 1.5 min

Learned in the rehearsal (2026-10-15):

* **CodeRabbit's free plan reviews about one PR per hour** on a public repo; the other PRs get a
  "Review limit reached" notice. So the video uses **one bug PR and no control PR**.
* **Pre-build this scene about 15 minutes before recording**, so the review is already there.
* The sandbox must have a `main` branch. If it was empty, the first pushed branch becomes the default
  branch and cleanup cannot delete it.

Pre-build (off camera, from `~/sdk1`):

```bash
sh demo/make_bug_repo.sh /tmp/demo-bugrepo-2
jev-harness reviewers build --repo /tmp/demo-bugrepo-2 --target-repo Adityakk9031/jev-harness-sandbox \
  --fix HEAD~1 --controls 0 --trigger-comment "@coderabbitai full review"          # type yes
```

Wait until CodeRabbit's inline comment appears on the PR (2 to 5 minutes). On camera:

```bash
jev-harness reviewers mine --repo /tmp/demo-bugrepo-2 --judge jev                  # Jev labels the fixes
```

Show the PR on GitHub: the diff (`and` turned back into `or` in `can_delete`) and CodeRabbit's comment.
Then:

```bash
jev-harness reviewers collect --reviewer 'coderabbitai[bot]' --wait 60
jev-harness reviewers report --judge jev --min-prs 1
```

The story the rehearsal produced (keep whatever your run shows): CodeRabbit's comment landed **exactly
on the bug line**, so by location it "caught" it, but it said *update the test to match*, accepting the
security bug as intended. Jev read the comment and was not convinced (`describes_bug` at 0.69, below the
0.70 trust bar, so reported as unclear). Location alone would have scored it a clean catch; that is why
`confirmed_recall` exists.

Off camera afterwards: `jev-harness reviewers cleanup --yes`.

---

**Close:** *"Test your agent setup like you test your code: guards, gates, memory, reviewers. Rules decide;
Jev advises; every number comes with its sample size."*

If anything on screen differs from the numbers above, keep the real ones. Do not edit results.
