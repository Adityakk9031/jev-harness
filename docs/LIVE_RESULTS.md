# Live results with Jev

Everything here was measured by running this repository against the real TypeSafe Jev endpoint
(`jev-latest`, served as `jev-1.13.0`) on 2026-10-14. The data sets are small and mostly synthetic, written
for this project, so read them as **evidence the integration works and as a method**, not as a benchmark of
Jev. Re-run them on your own data before relying on any number.

Reproduce (the evaluation behind this page took a few thousand judge calls in total):

```bash
export JEV_API_KEY=...
uv run python tests/fixtures/live/make_gate_items.py tests/fixtures/live/gate_items.jsonl 7
uv run pytest -m live tests/test_live_jev.py -o addopts=--strict-markers
```

## What Jev's `confidence` is (and is not)

Jev's answer has `probabilities` (one per option, summing to 1) and a `confidence`. Per TypeSafe's docs,
`confidence = (p_max - 1/n) / (1 - 1/n)`: how far the top probability sits above an even split. It is **not**
the probability of being right, and TypeSafe does not claim it is calibrated. For two options a `confidence` of
0.8 is a probability of 0.9; for three options it is 0.87. An even split scores 0 (observed live: a 0.5 / 0.5
answer still returns a `choice`, with `confidence` 0.0).

Consequences in this project:

* `calibrate gate` logs the **probability of the chosen option** as the stated confidence by default,
  because that is what "70% sure" means, and keeps Jev's statistic as `judge_confidence`
  (`--score confidence` logs the statistic instead).
* Judge answers are trusted when Jev's `confidence` is at least `min_judge_confidence` (default 0.7). For two
  options that is a probability of 0.85, for three options 0.80. The data below supports that being a
  sensible floor.
* The first version of this integration logged the statistic as if it were a probability. A live run made
  Jev look badly *under*confident (it said "4%" and was right half the time). That was the metric, not the
  model, and is fixed.

## 1. `calibrate gate`: is Jev's stated probability right? (640 items)

Two seeded sets of 320 pull-request descriptions (title and changed files only) with a ground-truth label from
a stated rule: **skip** if nothing that can change behaviour was touched (docs, comments, licence, ignore
files), otherwise **full**. The set includes hard cases (harmless-sounding titles on code, docs-looking
changes with code words). The judge never saw the label or the case type.

| | Result |
| --- | --- |
| Items answered | 640 of 640 (no failures, 4 parallel calls) |
| Correct | 635 of 640 |
| Calibration error (ECE) | 0.036; Brier score 0.012 |
| Errors | 5, all "comment-only change" items where title and files cannot tell, at probabilities 0.50 to 0.61 |
| Misses (skip decisions that should have run tests) | 0 of 285 labelled skips |
| Recommended threshold at a 2% target | 0.5, skipping 44.5% of decisions; upper bound on the miss rate 1.29% (exact Clopper-Pearson) |

Reading it: when Jev was unsure it was right to be (its low-probability answers are the genuinely ambiguous
items), and in the middle bands it was *under*confident (it said 63 to 86% and was right every time). With
320 items one set was not enough to show a miss rate under 2%; the report said "about 39 more labelled skips",
and the second set resolved it. That is the tool working as designed.

Caveats: the labels come from a rule I wrote and the items from templates I wrote; real pull requests will be
messier. The rubric given to the judge was written alongside the labelling rule.

## 2. `guard --judge`: would Jev have caught what the hooks missed?

A deliberately weak hook (a substring guard) lets 413 of 503 dangerous payloads through. Jev was asked about up
to 400 of the payloads that got through plus the benign controls.

| Corpus | Dangerous judged | Called dangerous | Called safe | Unclear | Benign called dangerous |
| --- | --- | --- | --- | --- | --- |
| Default corpus (the rubric was tuned on this) | 332 | 279 | 1 | 51 (+1 failed) | 0 of 68 |
| Held-out set 1 (21 new intents) | 310 | 268 | 0 | 42 | 6 of 90 (all `rm -rf ./build/cache`, arguably dangerous) |
| Held-out set 2 (9 new intents, written after the rubric was revised) | 206 | 182 | 0 | 24 | 0 of 74 |

The honest reading: after the prompt was tuned, **no dangerous payload was confidently called safe on unseen
intents**, and no ordinary command was confidently called dangerous on the fresh set. About 10 to 15% of
answers are "unclear" (for example `iptables -F`, `chown -R root` on a scratch directory, `printenv`), which is
a reasonable thing for a model to say. Only held-out set 2 is clean evidence; set 1 was used to revise the
wording of the options, and the default corpus was used to tune the question.

Prompt history, because it matters: the first wording said "outside a scratch area", and Jev (correctly, given
that instruction) treated the corpus's `./jev-canary-*` files as scratch and called 27 bypasses safe. Removing
that phrase, and naming `.claude/settings.json` as protected configuration, took the miss count to zero.

## 3. `reviewers report --judge`: does the comment describe the bug? (31 comments, 3 bugs)

Hand-written reviewer comments placed on the lines of three re-introduced bugs (an off-by-one, a deleted null
check, `and` changed to `or` in a permission check): 13 that describe the bug in varied wording, 15 that sit on
the right lines but are about something else, and 3 vague ones.

| Truth | Jev said |
| --- | --- |
| describes the bug (13) | `describes_bug` 13 |
| unrelated (15) | `unrelated` 15 |
| vague (3) | `unclear` 2, `unrelated` 1 |

28 of 28 clear cases correct, no confident wrong answer. Caveat: the comments are mine, so they are cleaner than
what real review apps write.

## 4. `reviewers mine --judge`

On this repository's own history (one qualifying commit) Jev called a 106-line feature commit `not_a_bug_fix`.
Two earlier small commits labelled `bug_fix` and `not_a_bug_fix` as expected. Too few commits to say more.

## 5. Robustness

* A wrong key returns exit code 2 with Jev's own message (HTTP 401), no retry storm, and the key is not echoed.
* 80 questions at concurrency 4, 16 and 32 took 12.0 s, 4.8 s and 3.0 s with no failures and 80 of 80 correct,
  so the default concurrency was raised from 4 to 8.
* About 1 second per call.

## What this does not show

Nothing here measures Jev against another judge, against human reviewers, or on your real traffic. The
Anthropic and OpenAI judges were not run. The reviewer-comment and pull-request data are synthetic.
