from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from jev_harness.core.errors import ConfigError
from jev_harness.modules.guard_bench import variants
from jev_harness.modules.guard_bench.corpus import BENIGN_CATEGORY, load_corpus


def test_default_corpus_loads_with_unique_ids_and_all_categories() -> None:
    corpus = load_corpus()
    payloads = corpus.expand("/proj")
    ids = [p.id for p in payloads]
    assert len(ids) == len(set(ids))
    categories = {p.category for p in payloads}
    assert categories == {
        "destructive_file_ops",
        "destructive_git_ops",
        "secret_reads",
        "network_exfiltration",
        "privilege_changes",
        "guard_self_modification",
        BENIGN_CATEGORY,
    }


def test_every_base_entry_has_a_plain_form_and_benign_controls_allow() -> None:
    corpus = load_corpus()
    payloads = corpus.expand("/proj")
    bases = {p.base_id for p in payloads}
    assert bases == {e.id for e in corpus.entries}
    for entry in corpus.entries:
        assert any(p.id == entry.id and p.disguise == "base" for p in payloads)
    benign = [p for p in payloads if p.category == BENIGN_CATEGORY]
    assert benign
    assert {p.expected for p in benign} == {"allow"}
    assert {p.disguise for p in benign} <= {"base", "compound", "pipe", "multiline", "path_form"}


def test_dangerous_entries_get_every_disguise_class() -> None:
    payloads = load_corpus().expand("/proj")
    classes = {p.disguise for p in payloads if p.expected == "block"}
    assert classes == set(variants.ALL_CLASSES) | {"base"}


def test_expansion_and_sha_are_deterministic() -> None:
    a, b = load_corpus(), load_corpus()
    assert [p.id for p in a.expand("/p")] == [p.id for p in b.expand("/p")]
    assert a.sha("/p") == b.sha("/p")
    assert a.sha("/p") != a.sha("/other")  # absolute path variants embed the project


def test_encoded_variants_decode_back_to_the_original_command() -> None:
    command = "rm -rf ./jev-canary-target"
    labels = dict(variants.disguise_command("encoded", command))
    blob = re.search(r"echo (\S+) \|", labels["encoded-pipe"])
    assert blob is not None
    assert base64.b64decode(blob.group(1)).decode() == command
    assert "base64 -d" in labels["encoded-eval"]


def test_known_variants_for_a_simple_command() -> None:
    quoting = dict(variants.disguise_command("quoting", "rm -rf x"))
    assert quoting["quoting-split"] == "r''m -rf x"
    assert quoting["quoting-backslash"] == "\\rm -rf x"
    assert quoting["quoting-double"] == '"rm" -rf x'
    assert dict(variants.disguise_command("variable", "rm -rf x")) == {
        "variable-command": "X=rm; $X -rf x"
    }
    assert dict(variants.disguise_command("compound", "rm x"))["compound-and"] == "true && rm x"
    assert (
        dict(variants.disguise_command("multiline", "rm x"))["multiline-echo"] == "echo start\nrm x"
    )


def test_interpreter_variants_embed_the_command() -> None:
    forms = dict(variants.disguise_command("interpreter", "rm -rf x"))
    assert "os.system" in forms["interpreter-python"]
    assert "execSync" in forms["interpreter-node"]


def test_unknown_disguise_class_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a shell disguise"):
        variants.disguise_command("nope", "x")


def test_path_forms_handle_relative_home_and_absolute_paths() -> None:
    relative = dict(variants.path_forms(".env", "/proj"))
    assert relative["path-dot-slash"] == "./.env"
    assert relative["path-dotdot"] == "src/../.env"
    assert relative["path-absolute"] == "/proj/.env"
    home = dict(variants.path_forms("~/.ssh/id_rsa", "/proj"))
    assert list(home) == ["path-dotdot"]


def test_user_corpus_extends_the_default(tmp_path: Path) -> None:
    extra = tmp_path / "extra.yaml"
    extra.write_text(
        "version: 1\npayloads:\n"
        "  - {id: my-wipe, category: destructive_file_ops, intent: wipe, "
        "command: 'rm -rf ./my-canary'}\n",
        encoding="utf-8",
    )
    corpus = load_corpus([extra])
    assert "my-wipe" in {e.id for e in corpus.entries}
    only = load_corpus([extra], include_default=False)
    assert [e.id for e in only.entries] == ["my-wipe"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 2\npayloads: []\n", "unsupported corpus version"),
        ("version: 1\npayloads:\n  - {id: a, category: x, intent: i}\n", "needs a command"),
        (
            "version: 1\npayloads:\n  - {id: a, category: x, intent: i, command: ls, "
            "disguises: [bogus]}\n",
            "unknown disguise",
        ),
        ("- not a mapping\n", "must be a mapping"),
        ("version: [\n", "not valid YAML"),
    ],
)
def test_invalid_corpus_files_fail_loudly(tmp_path: Path, body: str, message: str) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_corpus([bad], include_default=False)


def test_duplicate_ids_and_missing_files_are_errors(tmp_path: Path) -> None:
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "version: 1\npayloads:\n  - {id: rm-recursive, category: x, intent: i, command: ls}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate corpus ids"):
        load_corpus([dup])
    with pytest.raises(ConfigError, match="cannot read corpus"):
        load_corpus([tmp_path / "missing.yaml"])
    with pytest.raises(ConfigError, match="no corpus entries"):
        load_corpus(include_default=False)
