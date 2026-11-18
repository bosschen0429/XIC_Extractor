"""Build every list of a recipe from one blank RAW."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from xic_extractor.exclusion_list import ExclusionListError
from xic_extractor.exclusion_list.background import (
    Ms1Centroids,
    find_background_windows,
    top_n_counts,
    whole_run_windows,
)
from xic_extractor.exclusion_list.recipe import Recipe
from xic_extractor.exclusion_list.selection import (
    ExclusionList,
    ListSpec,
    select_exclusion_list,
)
from xic_extractor.ms1_scan_index import MS1Scan


@dataclass(frozen=True, slots=True)
class BuildResult:
    scan_ranges: tuple[tuple[float, float], ...]
    run_end: float
    lists: tuple[ExclusionList, ...]


def build_exclusion_lists(recipe: Recipe, dll_dir: Path) -> BuildResult:
    """Read the blank once, then select each list from its scan range."""
    from xic_extractor.raw_reader import open_raw

    with open_raw(recipe.blank, dll_dir) as handle:
        scan_ranges = handle.ms1_scan_mass_ranges()
        _check_scan_ranges(recipe.lists, scan_ranges)
        run_end = float(math.ceil(handle.acquisition_rt_bounds()[1]))
        centroids = _centroids(handle.read_ms1_scan_index(centroid=True))
    eluting = find_background_windows(
        centroids, run_end, recipe.settings.intensity_threshold
    )
    whole_run = whole_run_windows(eluting, run_end)
    lists = []
    for spec in recipe.lists:
        windows = eluting if spec.rt else whole_run
        lists.append(
            select_exclusion_list(
                spec,
                recipe.settings,
                recipe.named,
                recipe.targets,
                windows,
                top_n_counts(centroids, windows, spec.scan_range).tolist(),
                run_end,
            )
        )
    return BuildResult(scan_ranges, run_end, tuple(lists))


def _check_scan_ranges(
    specs: Sequence[ListSpec], acquired: Sequence[tuple[float, float]]
) -> None:
    for spec in specs:
        low, high = spec.scan_range
        if not any(a <= low and high <= b for a, b in acquired):
            ranges = ", ".join(f"{a:g}-{b:g}" for a, b in acquired)
            raise ExclusionListError(
                f"{spec.name}: scan_range {low:g}-{high:g} lies outside the blank's "
                f"MS1 coverage ({ranges})"
            )


def _centroids(scans: Sequence[MS1Scan]) -> Ms1Centroids:
    lengths = [len(scan.masses) for scan in scans]
    return Ms1Centroids(
        scan_rt=np.array([scan.rt for scan in scans], dtype=np.float64),
        scan=np.repeat(np.arange(len(scans), dtype=np.int64), lengths),
        mz=np.concatenate([scan.masses for scan in scans]),
        intensity=np.concatenate([scan.intensities for scan in scans]),
    )
