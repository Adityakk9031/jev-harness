"""Gate decision logs: loading, label merging, and the labelling sheet.

A log row is one decision by a gate: ``id``, ``decision`` (what the gate chose),
``confidence`` (how sure it said it was, 0 to 1), and optionally ``label`` (the
decision a human says was right), ``timestamp``, and any other fields, which are
kept as ``features``. A row is *correct* when ``decision == label``.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jev_harness.core.errors import ConfigError

SHEET_COLUMNS = (
    "id",
    "decision",
    "confidence",
    "label",
    "stratum",
    "stratum_size",
    "stratum_sampled",
)
RESERVED = {"id", "decision", "confidence", "label", "timestamp", "weight"}


class GateRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    decision: str
    confidence: float = Field(ge=0.0, le=1.0)
    label: str | None = None
    timestamp: datetime | None = None
    #: Inverse sampling probability. 1.0 unless the label came from a stratified sheet.
    weight: float = Field(default=1.0, gt=0.0)
    features: dict[str, Any] = Field(default_factory=dict)

    @property
    def labeled(self) -> bool:
        return self.label is not None

    @property
    def correct(self) -> bool:
        if self.label is None:
            raise ValueError(f"row {self.id} has no label")
        return self.decision == self.label


def _parse_timestamp(value: Any, where: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(f"{where}: timestamp {value!r} is not ISO 8601") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row(raw: Mapping[str, Any], where: str) -> GateRow:
    for required in ("id", "decision", "confidence"):
        if raw.get(required) in (None, ""):
            raise ConfigError(f"{where}: missing required field {required!r}")
    try:
        confidence = float(raw["confidence"])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: confidence {raw['confidence']!r} is not a number") from exc
    if not 0.0 <= confidence <= 1.0:
        raise ConfigError(
            f"{where}: confidence {confidence} is outside [0, 1]; "
            "divide percentages by 100 before logging"
        )
    label = raw.get("label")
    features = {k: v for k, v in raw.items() if k not in RESERVED and v not in (None, "")}
    return GateRow(
        id=str(raw["id"]),
        decision=str(raw["decision"]),
        confidence=confidence,
        label=None if label in (None, "") else str(label),
        timestamp=_parse_timestamp(raw.get("timestamp"), where),
        features=features,
    )


def _read_records(path: Path) -> list[tuple[str, dict[str, Any]]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"file not found: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    records: list[tuple[str, dict[str, Any]]] = []
    if path.suffix.lower() == ".csv":
        for number, row in enumerate(csv.DictReader(text.splitlines()), start=2):
            records.append((f"{path.name}:{number}", dict(row)))
        return records
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path.name}:{number}: not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ConfigError(f"{path.name}:{number}: each line must be a JSON object")
        records.append((f"{path.name}:{number}", value))
    return records


def load_log(path: Path) -> list[GateRow]:
    """Load a JSONL or CSV gate log. Duplicate ids and bad values are errors, not skips."""
    rows = [_row(raw, where) for where, raw in _read_records(path)]
    if not rows:
        raise ConfigError(f"{path} contains no rows")
    seen: set[str] = set()
    for row in rows:
        if row.id in seen:
            raise ConfigError(f"{path}: duplicate id {row.id!r}")
        seen.add(row.id)
    return rows


class MergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[GateRow]
    applied: int
    unmatched_ids: list[str]
    blank_labels: int


def merge_labels(rows: list[GateRow], labels_path: Path) -> MergeResult:
    """Attach labels from a sheet or label file by id. Unmatched ids are counted, not dropped."""
    by_id = {row.id: row for row in rows}
    labelled: dict[str, tuple[str, float]] = {}
    unmatched: list[str] = []
    blank = 0
    for where, raw in _read_records(labels_path):
        identifier = str(raw.get("id", "")).strip()
        if not identifier:
            raise ConfigError(f"{where}: missing id")
        label = raw.get("label")
        if label in (None, ""):
            blank += 1
            continue
        weight = 1.0
        size, sampled = raw.get("stratum_size"), raw.get("stratum_sampled")
        if size not in (None, "") and sampled not in (None, ""):
            try:
                weight = float(size) / float(sampled)
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                raise ConfigError(f"{where}: bad stratum_size/stratum_sampled") from exc
        if identifier not in by_id:
            unmatched.append(identifier)
            continue
        labelled[identifier] = (str(label).strip(), weight)

    merged = []
    for row in rows:
        if row.id in labelled:
            label, weight = labelled[row.id]
            merged.append(row.model_copy(update={"label": label, "weight": weight}))
        else:
            merged.append(row)
    return MergeResult(
        rows=merged, applied=len(labelled), unmatched_ids=unmatched, blank_labels=blank
    )


def bin_index(confidence: float, bins: int) -> int:
    """Equal-width bin of ``[0, 1]``; 1.0 belongs to the last bin."""
    return min(int(confidence * bins), bins - 1)


def stratum_name(row: GateRow, bins: int) -> str:
    index = bin_index(row.confidence, bins)
    low, high = index / bins, (index + 1) / bins
    return f"{row.decision}|{low:.2f}-{high:.2f}"


def sample_sheet(
    rows: list[GateRow], n: int, *, bins: int = 5, seed: int = 0
) -> list[dict[str, Any]]:
    """Sample ``n`` rows, stratified by decision and confidence bin.

    Allocation is equal per stratum, capped by stratum size, so rare strata (the confident skips
    that matter most) are not drowned by the common ones. Each sampled row records its stratum's
    population and sample sizes, which :func:`merge_labels` turns into weights so the report is
    not biased by the over-sampling.
    """
    if n < 1:
        raise ConfigError("--n must be at least 1")
    if any(row.labeled for row in rows):
        raise ConfigError(
            "the log already has labels; a sheet samples unlabeled logs. Use the labels directly "
            "or remove the label column before sampling"
        )
    strata: dict[str, list[GateRow]] = defaultdict(list)
    for row in rows:
        strata[stratum_name(row, bins)].append(row)

    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    remaining = {name: list(members) for name, members in sorted(strata.items())}
    for members in remaining.values():
        rng.shuffle(members)
    chosen: dict[str, list[GateRow]] = {name: [] for name in remaining}
    budget = min(n, len(rows))
    while budget > 0 and any(remaining.values()):
        for name in sorted(remaining):
            if budget == 0:
                break
            if remaining[name]:
                chosen[name].append(remaining[name].pop())
                budget -= 1

    sheet: list[dict[str, Any]] = []
    for name in sorted(chosen):
        for row in sorted(chosen[name], key=lambda r: r.id):
            entry: dict[str, Any] = {
                "id": row.id,
                "decision": row.decision,
                "confidence": row.confidence,
                "label": "",
                "stratum": name,
                "stratum_size": len(strata[name]),
                "stratum_sampled": len(chosen[name]),
            }
            for key, value in sorted(row.features.items()):
                entry[f"feature.{key}"] = value
            sheet.append(entry)
    return sheet


def write_sheet(sheet: Iterable[dict[str, Any]], path: Path) -> int:
    entries = list(sheet)
    extra = sorted({key for entry in entries for key in entry if key not in SHEET_COLUMNS})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*SHEET_COLUMNS, *extra], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)
    return len(entries)
