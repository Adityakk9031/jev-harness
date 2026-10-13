"""Importable summarizers for the --summarizer hook tests."""

from __future__ import annotations


def keep_codewords(messages: list[str]) -> str:
    """A summarizer that happens to keep every planted line, codewords and rules alike."""
    keep = ("Codeword for", "first line must be exactly")
    return "\n".join(
        line
        for message in messages
        for line in message.splitlines()
        if any(k in line for k in keep)
    )


def drop_everything(messages: list[str]) -> str:
    return "nothing to see"


def not_a_string(messages: list[str]) -> int:
    return 3
