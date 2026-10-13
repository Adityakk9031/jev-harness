from __future__ import annotations

import csv
from pathlib import Path

import pytest
from tests.modules.gate_calibration.gen import gate_rows, write_jsonl

from jev_harness.core.errors import ConfigError
from jev_harness.modules.gate_calibration.data import (
    load_log,
    merge_labels,
    sample_sheet,
    write_sheet,
)


def test_loads_jsonl_with_labels_timestamps_and_features(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text(
        '{"id": "a", "decision": "skip", "confidence": 0.9, "label": "skip", '
        '"timestamp": "2026-02-01T00:00:00Z", "pr": 12}\n\n'
        '{"id": "b", "decision": "run", "confidence": 0.4}\n',
        encoding="utf-8",
    )
    first, second = load_log(path)
    assert first.correct is True
    assert first.features == {"pr": 12}
    assert first.timestamp is not None
    assert first.timestamp.year == 2026
    assert second.labeled is False
    with pytest.raises(ValueError, match="no label"):
        _ = second.correct


def test_loads_csv(tmp_path: Path) -> None:
    path = tmp_path / "log.csv"
    path.write_text("id,decision,confidence,label\nx,skip,0.8,run\n", encoding="utf-8")
    (row,) = load_log(path)
    assert (row.id, row.confidence, row.correct) == ("x", 0.8, False)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('{"id": "a", "decision": "skip", "confidence": 95}\n', "outside \\[0, 1\\]"),
        ('{"id": "a", "decision": "skip", "confidence": "high"}\n', "not a number"),
        ('{"id": "a", "confidence": 0.5}\n', "missing required field 'decision'"),
        (
            '{"id": "a", "decision": "s", "confidence": 0.5}\n'
            '{"id": "a", "decision": "s", "confidence": 0.5}\n',
            "duplicate id",
        ),
        ("not json\n", "log.jsonl:1: not valid JSON"),
        ("[1]\n", "must be a JSON object"),
        ('{"id": "a", "decision": "s", "confidence": 0.5, "timestamp": "yesterday"}\n', "ISO 8601"),
        ("\n\n", "contains no rows"),
    ],
)
def test_bad_logs_fail_with_a_location(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "log.jsonl"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_log(path)


def test_missing_log_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file not found"):
        load_log(tmp_path / "nope.jsonl")


def test_merge_counts_unmatched_and_blank_labels_instead_of_dropping(tmp_path: Path) -> None:
    rows = load_log(write_jsonl(gate_rows([(0.9, 3, 3)], labeled=False), tmp_path / "l.jsonl"))
    labels = tmp_path / "labels.csv"
    labels.write_text("id,label\nr00001,skip\nr00002,run\nr00003,\nghost,skip\n", encoding="utf-8")
    merged = merge_labels(rows, labels)
    assert merged.applied == 2
    assert merged.blank_labels == 1
    assert merged.unmatched_ids == ["ghost"]
    assert [r.labeled for r in merged.rows] == [True, True, False]


def test_stratified_sheet_keeps_rare_confident_skips_and_is_reproducible(tmp_path: Path) -> None:
    rows_raw = gate_rows([(0.55, 900, 900), (0.99, 10, 10)], labeled=False)
    path = write_jsonl(rows_raw, tmp_path / "log.jsonl")
    rows = load_log(path)
    sheet = sample_sheet(rows, 20, bins=5, seed=3)
    assert len(sheet) == 20
    rare = [e for e in sheet if e["stratum"] == "skip|0.80-1.00"]
    assert len(rare) == 10  # equal allocation per stratum: the whole rare stratum is sampled
    assert {e["stratum_size"] for e in rare} == {10}
    assert sample_sheet(rows, 20, bins=5, seed=3) == sheet
    assert sample_sheet(rows, 20, bins=5, seed=4) != sheet

    sheet_path = tmp_path / "sheet.csv"
    assert write_sheet(sheet, sheet_path) == 20
    header = next(csv.reader(sheet_path.read_text().splitlines()))
    assert header[:4] == ["id", "decision", "confidence", "label"]


def test_labels_from_a_sheet_become_inverse_probability_weights(tmp_path: Path) -> None:
    rows = load_log(
        write_jsonl(
            gate_rows([(0.55, 900, 900), (0.99, 10, 10)], labeled=False), tmp_path / "l.jsonl"
        )
    )
    sheet = sample_sheet(rows, 20, bins=5, seed=0)
    for entry in sheet:
        entry["label"] = "skip"
    sheet_path = tmp_path / "sheet.csv"
    write_sheet(sheet, sheet_path)
    merged = merge_labels(rows, sheet_path)
    labelled = [r for r in merged.rows if r.labeled]
    # Weights reconstruct the population the sample was drawn from.
    assert sum(r.weight for r in labelled) == pytest.approx(910)


def test_sheet_refuses_an_already_labelled_log_and_bad_n(tmp_path: Path) -> None:
    rows = load_log(write_jsonl(gate_rows([(0.9, 5, 5)]), tmp_path / "l.jsonl"))
    with pytest.raises(ConfigError, match="already has labels"):
        sample_sheet(rows, 3)
    with pytest.raises(ConfigError, match="at least 1"):
        sample_sheet(rows, 0)
