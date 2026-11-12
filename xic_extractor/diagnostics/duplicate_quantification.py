"""Screen one Alignment output for one peak quantified in several Matrix rows.

Two published cells in one sample whose rows share a precursor m/z and whose
apices coincide measure the same MS1 signal, whatever their row identity.
This is a screening oracle over published values, not chemical identity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from xic_extractor.alignment_explanation.published_cells import PublishedCell
from xic_extractor.mass_accuracy import signed_ppm_error


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
    cells = [cell for cell in cells if cell.apex_rt_min is not None]
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
                if signed_ppm_error(right.row_mz, left.row_mz) > max_ppm:
                    break
                if (
                    left.row != right.row
                    and abs(_apex(left) - _apex(right)) * 60.0 <= max_apex_sec
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
        if abs(_apex(left[sample]) - _apex(right[sample])) * 60.0 > distinct_apex_sec
    ]
    if len(separated) < len(both_direct):
        return "view_duplicate"
    if separated:
        return "distinct_peaks_shared_backfill"
    return "complementary_split"


def _apex(cell: PublishedCell) -> float:
    if cell.apex_rt_min is None:
        raise ValueError("cell has no recorded apex")
    return cell.apex_rt_min
