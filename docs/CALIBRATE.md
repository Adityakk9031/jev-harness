# calibrate: when my gate says X% sure, how often is it right?

`jev-harness calibrate` checks a cheap "gate" (a router, a classifier, an LLM judge, Jev) against
decisions a human has verified, then tells you what confidence threshold is safe to skip the
expensive step at. It needs no model and no network.

## Input

A JSONL or CSV log with one row per decision:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Unique per row. |
| `decision` | yes | What the gate chose, for example `skip` or `run`. |
| `confidence` | yes | How sure the gate said it was, from 0 to 1 (not a percentage). |
| `label` | no | The decision a human says was right. A row is *correct* when `decision == label`. |
| `timestamp` | no | ISO 8601, needed only for `drift --split`. |
| anything else | no | Kept as features and copied into the labelling sheet. |

A **skip decision** is a row whose decision equals `--skip-decision` (default `skip`): the gate says the
expensive step can be avoided. A **miss** is a skip decision whose label says the step was needed.

## Quickstart

```bash
# 1. Sample decisions for a person to label (stratified by decision and confidence).
uv run jev-harness calibrate sheet gate.jsonl --out sheet.csv --n 100

# 2. Fill the `label` column of sheet.csv with the correct decision, then score it.
uv run jev-harness calibrate report gate.jsonl --labels sheet.csv --target 0.01

# 3. Evaluate the threshold you use today.
uv run jev-harness calibrate report gate.jsonl --labels sheet.csv --threshold 0.7

# 4. Did the gate change between two periods?
uv run jev-harness calibrate drift gate.jsonl --labels sheet.csv --split 2026-09-01T00:00:00Z
```

`--dry-run` validates the inputs and prints counts without scoring. Exit code `1` means a finding reached
`--fail-on` (default `flag`); exit code `2` means the inputs could not be used.

## What the report contains

* **Reliability table:** per confidence bin, how often the gate was right, with a Wilson interval.
* **Expected calibration error and Brier score.**
* **Per threshold:** share of all decisions skipped (from the whole log, labelled or not), misses among
  labelled skips, and an **upper confidence bound** on the miss rate. The bound is exact
  (Clopper-Pearson) when `jev-harness[stats]` is installed, otherwise Wilson, and the report says
  which. `risky_escape_rate` in the evidence is the other reading of "miss": the share of all risky
  items that were skipped.
* **Recommendation:** the cheapest threshold (the one that skips the most) whose upper bound is within
  `--target` (default 1%). If none qualifies it says whether more labels could help and how many more
  labelled skips would be needed at the observed miss rate, or whether the gate is simply not good
  enough at any threshold.

## Checks

| Rule id | Severity | Meaning |
| --- | --- | --- |
| `calibrate.ece_above_target` | flag (info on a small sample) | Calibration error above `--ece-target` (default 0.05). |
| `calibrate.overconfident_bucket` | flag | A bin with at least 10 labelled rows where even the interval's upper end is below the stated confidence. |
| `calibrate.miss_bound_breach` | escalate | Misses at `--threshold`, or at every threshold, are above the target. |
| `calibrate.insufficient_labels` | flag | Too few labels for the numbers to mean anything, or the target cannot yet be shown. |
| `calibrate.drift_detected` | flag | Per-bin accuracy moved between two windows (Holm-corrected). |

## What it does not prove

* The bound assumes labelled rows are a fair sample of what the gate will see. Labels picked because they
  looked interesting bias it.
* `calibrate sheet` over-samples rare strata on purpose. Labels merged from a sheet carry inverse-probability
  weights and the interval uses Kish's effective sample size, which is an approximation; the report
  says when it applied it.
* Unlabelled rows are counted and reported, never silently dropped or assumed correct.
* The numbers describe the gate on the data you labelled, not on future traffic.
