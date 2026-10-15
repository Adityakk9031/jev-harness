# jev-harness demo: shot list

Every terminal frame is the real output of the command shown at its top, run on 2026-10-15 against the
live Jev API and the live GitHub sandbox (raw text in the matching `.txt` file). Terminal frames are
rendered at 1920x1080 in a terminal style; the GitHub frames are real browser captures of
https://github.com/Adityakk9031/jev-harness-sandbox/pull/7. Keep the numbers as they are.

| # | File | Shows | Narration (suggested) |
| --- | --- | --- | --- |
| 1 | 01-judge-check-1.png | Jev is connected (answer, confidence, latency) | "jev-harness tests your AI agent setup like you test code. Jev is our judge; one call checks it's live." |
| 2 | 02-hook-1.png | A typical hand-written guard hook: a list of bad strings | "This is the guard most teams write first." |
| 3 | 03-guard-1.png, 03-guard-2.png | 331 of 503 disguised dangerous commands got through; 0 of 68 normal commands blocked; exact bypasses like `head -n 100 .env`, base64, `X=rm; $X` | "We throw 571 disguised commands at it. Nothing is executed. Two thirds get through." |
| 4 | 04-guard-jev-1.png, 04-guard-jev-2.png | Jev's opinion on the bypasses: 45 of 45 called dangerous, 0 of 20 normal commands flagged | "Jev reads through the disguises, but it only advises: the rules decide." |
| 5 | 05-gate-1.png | Jev used as a cheap gate on 40 pull requests | "Can you trust a cheap AI gate's confidence? Let's test Jev itself." |
| 6 | 06-gate-report-1.png | Reliability: says 99%, right 100%; honestly refuses a threshold with only 40 labels | "It's well calibrated, and the tool says exactly how many more labels it needs before trusting a threshold." |
| 7 | 07-compaction-1.png, 07-compaction-2.png | What survives context compaction (simulated, labelled): the path-scoped API rule is lost, root rules survive | "When memory fills up, rules vanish. This simulated run shows which ones." |
| 8 | 08-bugrepo-1.png | A small repo with three real bug fixes | "Now reviewers. Here's a history with three bugs that were fixed." |
| 9 | 09-mine-1.png | Jev labels all three as genuine bug fixes | "Jev finds the real bug fixes; a person still confirms." |
| 10 | 10-build-plan-1.png | The plan: one bug PR on the sandbox, with the CodeRabbit trigger | "We put one old bug back into a fresh pull request." |
| 11 | 11a-pr-summaries.png | The PR on GitHub with CodeRabbit's and cubic's summaries | "Two AI reviewers looked at it." |
| 12 | 11b-pr-cubic-review.png | cubic comments on the exact bug line: "update the test" | "cubic lands on the right line... and tells you to change the test." |
| 13 | 11c-pr-coderabbit-review.png | CodeRabbit on the same line: "update the test assertion to match that policy" | "CodeRabbit says the same: it treats a security bug as intended." |
| 14 | 12-pr-diff.png | The diff (`and` became `or`) with both comments | "One word turned 'admin AND owner' into 'admin OR owner'." |
| 15 | 13-collect-1.png | jev-harness collects both reviews | |
| 16 | 14-report-1.png | recall 1/1 for both by location, confirmed_recall 0/1: Jev was not convinced either explains the bug | "By location both caught it. Jev says neither actually explained it. That's why you need both numbers." |

Close: "Test your agent's guardrails, memory, gatekeepers and reviewers before they fail in production.
Rules decide, Jev advises, every number shows its sample size."
