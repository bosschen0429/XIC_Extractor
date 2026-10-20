"""Fail-closed path identity checks for artifact producers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def require_disjoint_artifact_paths(
    *,
    input_paths: Sequence[Path],
    output_paths: Sequence[Path],
    label: str,
) -> None:
    """Reject duplicate/aliased inputs and any input/output path collision."""

    resolved_inputs = tuple(path.resolve() for path in input_paths)
    resolved_outputs = tuple(path.resolve() for path in output_paths)
    _require_distinct_paths(
        resolved_inputs,
        label=f"{label} input artifact paths",
    )
    _require_distinct_paths(
        resolved_outputs,
        label=f"{label} output artifact paths",
    )
    collisions = {
        input_path
        for input_path in resolved_inputs
        for output_path in resolved_outputs
        if input_path == output_path or _same_existing_file(input_path, output_path)
    }
    if collisions:
        joined = ", ".join(
            str(path)
            for path in sorted(
                collisions,
                key=_canonical_path_sort_key,
            )
        )
        raise ValueError(f"{label} input/output paths must be disjoint: {joined}")


def _require_distinct_paths(paths: Sequence[Path], *, label: str) -> None:
    duplicates: set[Path] = set()
    for index, left in enumerate(paths):
        for right in paths[index + 1 :]:
            if left == right or _same_existing_file(left, right):
                duplicates.add(left)
    if duplicates:
        joined = ", ".join(
            str(path)
            for path in sorted(
                duplicates,
                key=_canonical_path_sort_key,
            )
        )
        raise ValueError(f"{label} must be distinct: {joined}")


def _same_existing_file(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _canonical_path_sort_key(path: Path) -> tuple[str, str]:
    text = str(path)
    return text.casefold(), text
