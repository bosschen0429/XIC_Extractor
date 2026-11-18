"""Write exclusion lists as Method Editor import CSVs and explain them in a report."""

from __future__ import annotations

import csv
import io
import statistics
from pathlib import Path

from xic_extractor.configuration.atomic_write import atomic_write_text
from xic_extractor.exclusion_list import ExclusionListError
from xic_extractor.exclusion_list.build import BuildResult
from xic_extractor.exclusion_list.recipe import REPORT_NAME, Recipe
from xic_extractor.exclusion_list.selection import (
    MZ_DIGITS,
    RT_DIGITS,
    WHOLE_RUN,
    ExclusionList,
    Target,
)

RT_COLUMNS = ["t start (min)", "t stop (min)"]


def write_exclusion_lists(result: BuildResult, recipe: Recipe) -> Path:
    """Write one import CSV per list and the report; return the report path."""
    for exclusion in result.lists:
        rt = exclusion.spec.rt
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Compound", "m/z", *(RT_COLUMNS if rt else [])])
        writer.writerows(
            [
                r.compound,
                f"{r.mz:.{MZ_DIGITS}f}",
                *(
                    [f"{r.start:.{RT_DIGITS}f}", f"{r.stop:.{RT_DIGITS}f}"]
                    if rt
                    else []
                ),
            ]
            for r in exclusion.rows
        )
        _publish(recipe.output_dir / exclusion.spec.csv_name, output.getvalue())
    report = recipe.output_dir / REPORT_NAME
    _publish(report, render_report(result, recipe))
    return report


def render_report(result: BuildResult, recipe: Recipe) -> str:
    settings = recipe.settings
    ranges = ", ".join(f"{low:g}-{high:g}" for low, high in result.scan_ranges)
    sources = len({target.source for target in recipe.targets})
    lines = [
        f"Blank: {recipe.blank.name} (MS1 {ranges}, run end {result.run_end:g} min)",
        f"Targets: {len(recipe.targets)} from {sources} file(s); "
        f"named exclusions: {len(recipe.named)}",
        f"Method: intensity threshold {settings.intensity_threshold:g}, "
        f"inclusion tolerance ±{settings.inclusion_tolerance_ppm:g} ppm, "
        f"max {settings.max_entries} entries",
        "Rule: no row's exclusion window overlaps a target's inclusion window "
        "while both are active.",
        "",
    ]
    for exclusion in result.lists:
        lines += _list_report(exclusion)
    return "\n".join(lines)


def list_summary(exclusion: ExclusionList) -> str:
    background = len(exclusion.rows) - exclusion.named_rows
    return (
        f"{exclusion.spec.csv_name}: {len(exclusion.rows)} rows "
        f"(named {exclusion.named_rows}, background {background}), "
        f"{len(exclusion.conflicts)} removed or cut to keep targets triggerable"
    )


def _list_report(exclusion: ExclusionList) -> list[str]:
    spec = exclusion.spec
    low, high = spec.scan_range
    lines = [
        f"== {list_summary(exclusion)}",
        f"scan range {low:g}-{high:g}, exclusion ±{spec.exclusion_tolerance_ppm:g} "
        f"ppm, {'with' if spec.rt else 'without'} RT",
    ]
    if apex := exclusion.background_apex:
        lines.append(
            f"background apex: lowest {min(apex):.3g}, "
            f"median {statistics.median(apex):.3g}"
        )
    lines += [
        f"  outside scan range: {entry.name} {entry.mz:.6f}"
        for entry in exclusion.named_outside_range
    ]
    for conflict in exclusion.conflicts:
        kept = (
            "kept " + ", ".join(f"{a:.2f}-{b:.2f} min" for a, b in conflict.kept)
            if conflict.kept
            else "removed"
        )
        targets = "; ".join(_target_text(target) for target in conflict.targets)
        lines.append(f"  {conflict.candidate}: {kept} <- {targets}")
    lines.append("")
    return lines


def _target_text(target: Target) -> str:
    name = f"{target.name} " if target.name else ""
    span = (
        ""
        if (target.start, target.stop) == WHOLE_RUN
        else f", {target.start:g}-{target.stop:g} min"
    )
    return f"{name}{target.mz:g} ({target.source}{span})"


def _publish(path: Path, text: str) -> None:
    try:
        atomic_write_text(path, text)
    except PermissionError:
        raise ExclusionListError(
            f"cannot overwrite {path.name}; close it (e.g. in Excel) and run again"
        ) from None
