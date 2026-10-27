"""Screen one Alignment output for one peak quantified in several Matrix rows.

Two published cells in one sample whose rows share a precursor m/z and whose
apices coincide measure the same MS1 signal, whatever their row identity.
This is a screening oracle over published values, not chemical identity.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics.alignment_explanation import (
    open_review_population,
    read_explanation_metadata,
)
from xic_extractor.output.alignment.output_levels import EXPLANATION_ARTIFACT_NAME

_DIRECT_ORIGINS = frozenset({"discovery_support", "reacquired_support"})


@dataclass(frozen=True)
class PublishedCell:
    row: int
    row_mz: float
    sample: str
    direct: bool
    apex_rt_min: float


def read_published_cells(alignment_dir: Path) -> tuple[PublishedCell, ...]:
    """Published Matrix cells with the apex of the peak each one quantifies."""
    identity = _read_tsv(alignment_dir / "alignment_review.tsv")
    rows = {
        record["feature_id"]: (
            int(record["matrix_row_index"]),
            float(record["matrix_mz"]),
        )
        for record in identity
        if record["matrix_row_index"]
    }
    cell_ledger = alignment_dir / "alignment_backfill_cell_evidence.tsv"
    if not cell_ledger.is_file():
        cell_ledger = alignment_dir / "alignment_cells.tsv"
    backfill_apex = {
        (record["feature_id"], record["sample_stem"]): float(record["apex_rt"])
        for record in _read_tsv(cell_ledger)
        if record["apex_rt"] and record["feature_id"] in rows
    }
    explanation = alignment_dir / EXPLANATION_ARTIFACT_NAME
    metadata = read_explanation_metadata(
        explanation, selection_only=True, query_only=True
    )
    samples = {
        sample["sample_key"]: sample["sample_stem"]
        for sample in metadata.summary["samples"]
    }
    observation_apex = {
        observation["physical_peak_key"]: observation["rt_apex_min"]
        for subject in metadata.records.get("terminal_subjects", {}).values()
        for observation in subject.get("observations") or ()
    }
    cells = []
    with open_review_population(explanation, metadata.summary) as population:
        for entry in population:
            group = entry["row"]["feature_id"]
            if group not in rows:
                continue
            row, row_mz = rows[group]
            for cell in entry["cells"]:
                if cell["value"] is None:
                    continue
                sample = samples[cell["sample_key"]]
                apex = _cell_apex(cell, observation_apex, backfill_apex, group, sample)
                if apex is not None:
                    cells.append(
                        PublishedCell(
                            row=row,
                            row_mz=row_mz,
                            sample=sample,
                            direct=cell["support_origin"] in _DIRECT_ORIGINS,
                            apex_rt_min=apex,
                        )
                    )
    return tuple(cells)


def summarize_duplicate_quantification(
    cells: Sequence[PublishedCell],
    *,
    max_ppm: float = 10.0,
    max_apex_sec: float = 3.0,
    distinct_apex_sec: float = 6.0,
) -> dict[str, Any]:
    """Count same-sample cross-row pairs that quantify one apex.

    Row pairs are classified by their direct detections: complementary rows
    (no sample directly detected in both) suggest one split feature; a sample
    with both rows directly detected at separate apices shows distinct peaks
    with a shared Backfill; both rows directly detecting one apex is a
    duplicate view.
    """
    pairs = _duplicate_pairs(cells, max_ppm=max_ppm, max_apex_sec=max_apex_sec)
    by_row: dict[int, dict[str, PublishedCell]] = defaultdict(dict)
    for cell in cells:
        by_row[cell.row][cell.sample] = cell
    row_pairs: dict[tuple[int, int], int] = Counter(
        (min(a.row, b.row), max(a.row, b.row)) for a, b in pairs
    )
    categories: Counter[str] = Counter()
    category_cells: Counter[str] = Counter()
    for (left, right), count in row_pairs.items():
        category = _row_pair_category(
            by_row[left], by_row[right], distinct_apex_sec=distinct_apex_sec
        )
        categories[category] += 1
        category_cells[category] += count
    return {
        "evidence": "diagnostic_only",
        "criteria": {
            "max_ppm": max_ppm,
            "max_apex_sec": max_apex_sec,
            "distinct_apex_sec": distinct_apex_sec,
        },
        "matrix_rows": len(by_row),
        "valued_cells": len(cells),
        "duplicate_cell_pairs": len(pairs),
        "duplicate_row_pairs": len(row_pairs),
        "rows_involved": len({row for pair in row_pairs for row in pair}),
        "cell_pair_kinds": dict(
            Counter(
                "".join(sorted(("D" if a.direct else "B") + ("D" if b.direct else "B")))
                for a, b in pairs
            )
        ),
        "row_pair_categories": dict(categories),
        "cell_pair_categories": dict(category_cells),
    }


def _duplicate_pairs(
    cells: Iterable[PublishedCell], *, max_ppm: float, max_apex_sec: float
) -> list[tuple[PublishedCell, PublishedCell]]:
    by_sample: dict[str, list[PublishedCell]] = defaultdict(list)
    for cell in cells:
        by_sample[cell.sample].append(cell)
    pairs = []
    for sample_cells in by_sample.values():
        ordered = sorted(sample_cells, key=lambda cell: (cell.row_mz, cell.row))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if (right.row_mz - left.row_mz) / left.row_mz * 1e6 > max_ppm:
                    break
                if (
                    left.row != right.row
                    and abs(left.apex_rt_min - right.apex_rt_min) * 60.0 <= max_apex_sec
                ):
                    pairs.append((left, right))
    return pairs


def _row_pair_category(
    left: Mapping[str, PublishedCell],
    right: Mapping[str, PublishedCell],
    *,
    distinct_apex_sec: float,
) -> str:
    both_direct = [
        sample
        for sample in left.keys() & right.keys()
        if left[sample].direct and right[sample].direct
    ]
    separated = [
        sample
        for sample in both_direct
        if abs(left[sample].apex_rt_min - right[sample].apex_rt_min) * 60.0
        > distinct_apex_sec
    ]
    if len(separated) < len(both_direct):
        return "view_duplicate"
    if separated:
        return "distinct_peaks_shared_backfill"
    return "complementary_split"


def _cell_apex(
    cell: Mapping[str, Any],
    observation_apex: Mapping[str, float],
    backfill_apex: Mapping[tuple[str, str], float],
    group: str,
    sample: str,
) -> float | None:
    for observation in cell["observation_ids"]:
        if observation in observation_apex:
            return float(observation_apex[observation])
    return backfill_apex.get((group, sample))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))
