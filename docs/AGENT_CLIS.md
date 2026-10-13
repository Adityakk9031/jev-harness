# Agent CLIs — what jev-harness relies on

Every flag, path, and output field this package depends on is listed here, with **where it came
from** and **whether it has been verified in this repository**. Agent CLIs change without notice;
if an expectation below is wrong, the harness must fail loudly (exit 2), never score zero.

Status labels:

* **observed** — produced by a run or a file examined in this repo (dated).
* **documented** — read from vendor docs on 2026-10-13 (URLs below); not yet executed here.
* **unverified** — no source confirms it; the code path either refuses or is fixture-only.

---

## 1. Environment on the build machine (spike, 2026-10-13)

| Check | Result |
| --- | --- |
| `claude` on PATH | **not installed** |
| `codex` on PATH | **not installed** |
| `~/.codex` | absent (no rollout sessions to sample) |
| `~/.claude/settings.json` | absent (no real user hooks/permission rules to discover) |
| `~/.claude/projects/*.jsonl` | **2 real transcripts present** |

Consequences, accepted rather than papered over:

* Runner argv is built from vendor docs and pinned by unit tests (`tests/core/test_runner.py`),
  but **no live `claude`/`codex` run has happened in this repo**. Every `@pytest.mark.live` path
  stays skipped until a machine with the CLIs runs them.
* The Claude Code importer's record shape below is **observed**; tool-call handling is not.

### Observed Claude Code transcript shape (structure only)

Files: `~/.claude/projects/<project-slug>/<session-id>.jsonl`, one JSON record per line.

* Conversational records have `type ∈ {user, assistant, system}`, a `message` object with `role`
  and `content` (string or list of typed blocks), `timestamp`, `cwd`, `sessionId`.
* Assistant records carry `message.usage` (`input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`), `version` (client version), and
  `isSidechain` for subagent turns.
* Non-conversational record types exist and are **skipped and counted**:
  `queue-operation`, `attachment`, `last-prompt`, `custom-title`, `agent-name`,
  `file-history-snapshot`, `atis-latch`, `cost-state`.
* **Not observed:** no tool-call or tool-result records appeared in the sample. `tool_use` /
  `tool_result` block handling follows the public Anthropic content-block shape and is covered by
  the synthetic fixture `tests/fixtures/claude_code/demo-tools.jsonl` only.
  `Trace.meta["tool_blocks_observed"]` records which case a load was.

---

## 2. Claude Code (`claude`)

Docs: <https://code.claude.com/docs/en/headless>, <https://code.claude.com/docs/en/cli-reference>,
<https://code.claude.com/docs/en/hooks-guide>, <https://code.claude.com/docs/en/context-window>.

### Headless run — `ClaudeCodeRunner` (documented; not executed here)

```
claude -p <prompt> --output-format json
       [--model <m>] [--max-turns <n>] [--permission-mode <m>] [--resume <session-id>]
       [runner.extra_args...]
claude --version            # availability probe
```

Relied-on facts:

* `-p`/`--print` is headless; exit 0 on success, non-zero on failure — a non-zero exit with an
  unparseable JSON envelope is a **harness error**, not an empty transcript.
* `--output-format json` result envelope includes `total_cost_usd` (client-side estimate) and
  `result` (final text). Cost unknown ⇒ recorded as unknown (never estimated by us).
* `--resume <uuid>` continues a session; `--permission-mode` selects permission behaviour.
* Failure surface: if the envelope lacks the fields we read, `ClaudeCodeRunner` raises rather
  than synthesizing turns (`runner.py`, `_trace_from_envelope`).

### Hooks contract (used as the reference shape for guard_bench's generic adapter)

* `PreToolUse` stdin JSON: `{"session_id", "cwd", "hook_event_name", "tool_name", "tool_input"}`.
* Structured decision:
  `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow|deny|ask",
  "permissionDecisionReason": "..."}}`.
* **Exit code 0 is not approval**; exit code 2 blocks. `PostToolUse`/`Stop` use a top-level
  `decision: "block"` instead.
* Events include `SessionStart` (sources incl. `compact`), `PreCompact`, `PostCompact`.

### Instruction sources (compaction_survival discovery)

Root `CLAUDE.md`, `./.claude/CLAUDE.md`, `CLAUDE.local.md`, nested files (on demand),
`@` imports (≤ 4 hops), `.claude/rules/*.md` with optional `paths:` frontmatter, skills
(`SKILL.md`), subagents (`~/.claude/agents/`, `.claude/agents/`).
After compaction: project-root CLAUDE.md, unscoped rules and auto memory are **re-injected**;
path-scoped rules and nested files reload on demand — this asymmetry is the module's hypothesis.

### Compaction trigger — **unverified (OQ-3)**

Candidates, none confirmed to work under `-p`: (a) the prompt `/compact`, (b) the
`--autocompact` flag (v2.1.221+), (c) `CLAUDE_CODE_AUTO_COMPACT_WINDOW` /
`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` to force an auto-compact. `PreCompact`/`PostCompact` hooks can
*detect* a compaction whatever the trigger. Until a live run confirms one, `compaction run`
requires `--mode simulated` or refuses the real mode with an explicit message.

---

## 3. Codex (`codex`)

Docs: <https://developers.openai.com/codex/non-interactive-mode>,
<https://developers.openai.com/codex/agent-configuration/agents-md>,
<https://developers.openai.com/codex/config-reference>.

### Headless run — `CodexRunner` (documented; not executed here)

```
codex exec <prompt> --json --cd <workspace>
         [--model <m>] [--sandbox <read-only|workspace-write|danger-full-access>]
         [--ask-for-approval <p>] [runner.extra_args...]
codex exec resume <session-id> <prompt> --json --cd <workspace>   # resume
codex --version             # availability probe
```

Relied-on facts:

* `exec` and `--json` are labelled **Stable**; the stream is JSONL events:
  `thread.started`, `turn.started`, `item.started|completed`,
  `turn.completed` with `usage.{input_tokens,cached_input_tokens,output_tokens}`, `turn.failed`,
  `error`. Unrecognized event/item types are **counted in `Trace.meta`**, never dropped silently.
* Codex reports tokens, not dollars: cost stays `None` unless a price table is supplied (D10).
* `--sandbox`, `--ask-for-approval`, `--cd` are how a run stays inside the disposable workspace.

### Session files — `CodexImporter` (repository evidence, not a stable API)

`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`. Storage layout is an implementation detail;
the importer reads it tolerantly and refuses a file with no recognizable turns.

### Hooks — **deferred (D7)**

Hooks exist (`hooks.json` / `[hooks]`) but carry no explicit Stable maturity label and require
trust for non-managed hooks. v1 does not build on them; see `docs/ROADMAP.md`.

---

## 4. contextleleo (optional importer)

Docs: <https://github.com/Adityakk9031/contextleleo/blob/main/docs/usage.md>.

* `contextleleo export <id>[#range] [--from <harness>] [--out <file>]` → **Simple JSON document**
  (`{meta, messages}` with typed blocks) on stdout or `--out`. This is what
  `ContextleleoImporter` shells out to (`export_argv()` is asserted by tests).
* `contextleleo list` has **no documented JSON mode** — the importer **refuses** to scrape it
  (exit 2 with guidance) instead of guessing.
* The brief's assumed `contextleleo export --format json <path>` form does **not** exist (D12).
* Not installed on the build machine; the bridge is covered by a fake subprocess runner in tests.

---

## 5. Jev / TypeSafe judge endpoint

`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`, body
`{state, model: "jev-latest", questions}`; each question here is a single `choice`
(`instructions` + `criteria`), because **confidence exists only on `choice`/`score` answers** —
that is why `JevJudge` never asks anything else (D3). Errors: 401/422 no retry; 429/5xx/529
retry with capped backoff; every failure becomes an `escalated` Decision (never a pass).
Env overrides: `JEV_API_URL`, `JEV_MODEL`, `JEV_API_KEY` / `TYPESAFE_API_KEY`.

---

## 6. Maintenance rules

1. Read `--help` and the vendor docs **at build time** on a machine with the CLI installed;
   update this file in the same commit as any flag change.
2. Anything labelled *documented* here must get an *observed* entry the first time it runs live
   (paste the command and the outcome into the PR/commit message).
3. When output we expect is missing, raise (`ImporterError` / `HarnessError`, exit 2). Never
   return an empty trace or a zero rate — an empty trace scores as "nothing survived", which is
   a measurement of nothing.
4. Keep `tests/core/test_runner.py` argv assertions in sync with this file; they are the
   executable copy of section 2/3.
