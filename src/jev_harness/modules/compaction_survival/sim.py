"""Simulated compaction: a pluggable summarizer applied to a synthetic session.

This measures the *summarizer*, not any real agent. It exists so the whole pipeline (discovery,
planting, probing, scoring, reporting) can be tested offline, and so two compaction strategies can
be compared on the same sources. Results from this module are always labelled ``simulated``.
"""

from __future__ import annotations

import importlib
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from jev_harness.core.errors import ConfigError
from jev_harness.modules.compaction_survival.canary import Plan, canary_text
from jev_harness.modules.compaction_survival.results import Observation, StrategyRun

FILLER_WORDS = (
    "refactor",
    "module",
    "test",
    "handler",
    "config",
    "parser",
    "cache",
    "retry",
    "queue",
    "index",
    "schema",
    "endpoint",
    "session",
    "metric",
    "token",
    "buffer",
    "loader",
    "router",
    "worker",
    "patch",
)
CODEWORD_PATTERN = re.compile(r"\b[A-Z]+-[A-Z]+-\d{4}\b")
HEADER_RULE = re.compile(r"first line must be exactly: (# jev-header-\S+)")


@dataclass(slots=True)
class Message:
    role: str
    text: str
    #: The instruction source this message carries, if any.
    source_id: str | None = None
    reinjectable: bool = False


Summarizer = Callable[[list[Message]], list[Message]]


@dataclass(slots=True)
class Strategy:
    name: str
    summarize: Summarizer
    description: str
    deterministic: bool = True


def keep_recent(keep: int = 10) -> Strategy:
    """Drop everything but the last ``keep`` messages."""

    def apply(context: list[Message]) -> list[Message]:
        return context[-keep:]

    return Strategy("keep-recent", apply, f"keeps only the last {keep} messages and drops the rest")


def extractive(chars_per_message: int = 80) -> Strategy:
    """One summary message made of the first characters of every message."""

    def apply(context: list[Message]) -> list[Message]:
        lines = [f"[{m.role}] {m.text[:chars_per_message]}" for m in context]
        return [Message("system", "Summary of the conversation so far:\n" + "\n".join(lines))]

    return Strategy(
        "extractive",
        apply,
        f"summarises each message by its first {chars_per_message} characters",
    )


def claude_code_documented(chars_per_message: int = 80) -> Strategy:
    """A *model of Claude Code's documented behavior*, not a measurement of it.

    The docs say root CLAUDE.md, unscoped rules, and memory are re-injected after compaction,
    while path-scoped rules and nested files are reloaded on demand. Sources whose documentation
    is silent are treated as lost, which is the conservative reading.
    """
    base = extractive(chars_per_message)

    def apply(context: list[Message]) -> list[Message]:
        kept = [m for m in context if m.reinjectable]
        rest = [m for m in context if not m.reinjectable]
        return [*base.summarize(rest), *kept]

    return Strategy(
        "claude-code-documented",
        apply,
        "extractive summary plus re-injection of the sources Claude Code's docs say are "
        "re-injected (a model of the documentation, not of the agent)",
    )


BUILTIN: dict[str, Callable[[], Strategy]] = {
    "keep-recent": keep_recent,
    "extractive": extractive,
    "claude-code-documented": claude_code_documented,
}


def load_custom_summarizer(spec: str) -> Strategy:
    """Import ``package.module:function``. The function maps a list of strings to a summary string.

    The imported code runs with your privileges; only point this at code you wrote or reviewed.
    """
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise ConfigError(f"--summarizer must look like package.module:function, got {spec!r}")
    try:
        function = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ConfigError(f"cannot load summarizer {spec!r}: {exc}") from exc

    def apply(context: list[Message]) -> list[Message]:
        summary = function([m.text for m in context])
        if not isinstance(summary, str):
            raise ConfigError(f"summarizer {spec!r} must return a string, got {type(summary)}")
        return [Message("system", summary)]

    return Strategy(f"custom:{spec}", apply, f"your summarizer {spec}", deterministic=False)


def resolve_strategies(names: list[str], custom: str | None) -> list[Strategy]:
    strategies: list[Strategy] = []
    for name in names:
        if name not in BUILTIN:
            raise ConfigError(f"unknown strategy {name!r}; built-ins: {', '.join(sorted(BUILTIN))}")
        strategies.append(BUILTIN[name]())
    if custom:
        strategies.append(load_custom_summarizer(custom))
    if not strategies:
        raise ConfigError("no compaction strategy selected")
    return strategies


def filler_messages(rng: random.Random, count: int, words: int = 30) -> list[Message]:
    return [
        Message(
            "user" if i % 2 == 0 else "assistant",
            " ".join(rng.choice(FILLER_WORDS) for _ in range(words)),
        )
        for i in range(count)
    ]


def build_context(
    plan: Plan,
    *,
    filler_turns: int,
    seed: int,
    texts: dict[str, str] | None = None,
) -> list[Message]:
    """Session start: every probed source's text, then ``filler_turns`` of ordinary work.

    ``texts`` maps source id to the full text with its canary; without it only the canary lines
    are used, which is enough for tests that do not care about position.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible filler, not security
    context = [
        Message(
            "system",
            f"[{c.source.path}]\n{(texts or {}).get(c.source.id, canary_text(c))}",
            source_id=c.source.id,
            reinjectable=c.source.documented_reinjected is True,
        )
        for c in plan.canaries
    ]
    context.extend(filler_messages(rng, filler_turns))
    return context


@dataclass(slots=True)
class Recall:
    codewords: set[str] = field(default_factory=set)
    headers: set[str] = field(default_factory=set)


def probe(context: list[Message]) -> Recall:
    """What a perfectly literal agent could answer from this context alone."""
    text = "\n".join(m.text for m in context)
    return Recall(set(CODEWORD_PATTERN.findall(text)), set(HEADER_RULE.findall(text)))


def run_strategy(
    strategy: Strategy,
    plan: Plan,
    *,
    rounds: int,
    trials: int,
    filler_turns: int = 40,
    seed: int = 0,
    texts: dict[str, str] | None = None,
) -> StrategyRun:
    """Measure every source after 0..rounds compactions, ``trials`` times.

    Deterministic strategies give the same answer every trial, so they run once and say so: five
    identical trials would be five copies of one observation, not evidence.
    """
    effective_trials = 1 if strategy.deterministic else trials
    run = StrategyRun(
        name=strategy.name,
        mode="simulated",
        deterministic=strategy.deterministic,
        rounds=rounds,
        trials=effective_trials,
        notes=[strategy.description],
    )
    if strategy.deterministic and trials != 1:
        run.notes.append(
            f"deterministic strategy: ran 1 trial instead of {trials}, because identical trials "
            "would not be independent evidence"
        )
    for trial in range(effective_trials):
        context = build_context(plan, filler_turns=filler_turns, seed=seed + trial, texts=texts)
        for round_ in range(rounds + 1):
            if round_ > 0:
                context = strategy.summarize(context)
            recall = probe(context)
            for canary in plan.canaries:
                run.observations.append(
                    Observation(
                        source_id=canary.source.id,
                        round=round_,
                        trial=trial,
                        recalled=canary.codeword in recall.codewords,
                        behavior_followed=(
                            canary.header in recall.headers if canary.header is not None else None
                        ),
                    )
                )
    return run
