"""Read an exclusion-list recipe and the target and named-exclusion files it cites.

Paths in a recipe are relative to the recipe file. Target files are the
inclusion lists the methods load (CSV with an ``m/z`` column, optional
``Compound`` and ``t start (min)``/``t stop (min)``). Named exclusions are a CSV
or an Excel sheet with an ``m/z`` column and a name; rows named ``x`` or left
blank are skipped.
"""

from __future__ import annotations

import csv
import glob
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.exclusion_list import ExclusionListError
from xic_extractor.exclusion_list.selection import (
    WHOLE_RUN,
    ListSpec,
    MethodSettings,
    NamedExclusion,
    Target,
)
from xic_extractor.tabular_io import optional_float
from xic_extractor.validation_primitives import require_finite_real, require_integer

DEFAULT_SETTINGS = MethodSettings(
    intensity_threshold=25000.0, inclusion_tolerance_ppm=20.0, max_entries=1000
)
DEFAULT_EXCLUSION_TOLERANCE_PPM = 20.0
_NAME_HEADERS = {"compound", "name"}
_UNNAMED = {"", "x"}
_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


REPORT_NAME = "exclusion_report.txt"


@dataclass(frozen=True, slots=True)
class Recipe:
    blank: Path
    output_dir: Path
    targets: tuple[Target, ...]
    named: tuple[NamedExclusion, ...]
    settings: MethodSettings
    lists: tuple[ListSpec, ...]


def read_recipe(path: Path, output_dir: Path | None = None) -> Recipe:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ExclusionListError(f"{path.name}: {exc}") from exc
    base = path.parent
    _only_keys(
        document, {"blank", "targets", "named_exclusions", "method", "lists"}, "recipe"
    )
    blank = base / _required(document, "blank", str, "recipe")
    if not blank.is_file():
        raise ExclusionListError(f"blank RAW not found: {blank}")
    lists = tuple(
        _list_spec(table) for table in _required(document, "lists", list, "recipe")
    )
    if not lists:
        raise ExclusionListError("recipe: add at least one [[lists]] table")
    output_dir = output_dir or base
    outputs = {(output_dir / spec.csv_name).resolve() for spec in lists}
    if len(outputs) != len(lists):
        raise ExclusionListError("recipe: every [[lists]] output path must be unique")
    outputs.add((output_dir / REPORT_NAME).resolve())
    _check_input_path(path, outputs)
    _check_input_path(blank, outputs)
    targets = tuple(
        target
        for pattern in _required(document, "targets", list, "recipe")
        for file in _matching_files(base, pattern, outputs)
        for target in read_targets(file)
    )
    named = tuple(
        entry
        for source in document.get("named_exclusions", [])
        for entry in _read_named_source(base, source, outputs)
    )
    return Recipe(
        blank, output_dir, targets, named, _settings(document.get("method", {})), lists
    )


def read_targets(path: Path) -> tuple[Target, ...]:
    rows = _csv_rows(path)
    header = [cell.strip().lower() for cell in rows[0]] if rows else []
    if "m/z" not in header:
        raise ExclusionListError(f"{path.name}: needs an 'm/z' column")
    mz_col = header.index("m/z")
    name_col = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), None)
    start_col = _optional_index(header, "t start (min)")
    stop_col = _optional_index(header, "t stop (min)")
    if (start_col is None) != (stop_col is None):
        raise ExclusionListError(
            f"{path.name}: give both 't start (min)' and 't stop (min)' or neither"
        )
    targets = []
    for line, row in enumerate(rows[1:], 2):
        if not any(cell.strip() for cell in row):
            continue
        row = row + [""] * (len(header) - len(row))
        mz = _number(row[mz_col], f"{path.name} line {line} m/z")
        start, stop = WHOLE_RUN
        if (
            start_col is not None
            and stop_col is not None
            and (row[start_col].strip() or row[stop_col].strip())
        ):
            start = _number(row[start_col], f"{path.name} line {line} t start")
            stop = _number(row[stop_col], f"{path.name} line {line} t stop")
            if not 0 <= start < stop:
                raise ExclusionListError(
                    f"{path.name} line {line}: require 0 <= t start < t stop"
                )
        name = row[name_col].strip() if name_col is not None else ""
        targets.append(Target(name, mz, start, stop, path.name))
    return tuple(targets)


def read_named_exclusions(
    path: Path, sheet: str | None = None
) -> tuple[NamedExclusion, ...]:
    rows: Sequence[Sequence[Any]]
    if path.suffix.lower() in _EXCEL_SUFFIXES:
        rows = _sheet_rows(path, sheet)
        source = f"{path.name} [{sheet}]"
    else:
        rows = _csv_rows(path)
        source = path.name
    headers = [[str(cell or "").strip().lower() for cell in row] for row in rows]
    top = next((i for i, header in enumerate(headers) if "m/z" in header), None)
    if top is None:
        raise ExclusionListError(f"{source}: needs an 'm/z' column")
    header = headers[top]
    mz_col = header.index("m/z")
    name_col = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), mz_col + 1)
    entries = []
    for line, row in enumerate(rows[top + 1 :], top + 2):
        cells = list(row) + [None] * (name_col + 1 - len(row))
        name = re.sub(r"\s+", " ", str(cells[name_col] or "")).strip()
        if name.lower() in _UNNAMED or cells[mz_col] in (None, ""):
            continue
        entries.append(
            NamedExclusion(name, _number(cells[mz_col], f"{source} row {line} m/z"))
        )
    return tuple(entries)


def _read_named_source(
    base: Path, source: Any, outputs: set[Path]
) -> tuple[NamedExclusion, ...]:
    if not isinstance(source, dict):
        raise ExclusionListError(
            'named_exclusions: write each entry as { file = "...", sheet = "..." }'
        )
    _only_keys(source, {"file", "sheet"}, "named_exclusions")
    path = base / _required(source, "file", str, "named_exclusions")
    _check_input_path(path, outputs)
    if not path.is_file():
        raise ExclusionListError(f"named exclusion file not found: {path}")
    return read_named_exclusions(path, source.get("sheet"))


def _list_spec(table: Any) -> ListSpec:
    if not isinstance(table, dict):
        raise ExclusionListError("lists: each entry must be a [[lists]] table")
    _only_keys(
        table, {"name", "scan_range", "exclusion_tolerance_ppm", "rt"}, "[[lists]]"
    )
    name = _required(table, "name", str, "[[lists]]")
    if not re.fullmatch(r"[\w.+-]+", name):
        raise ExclusionListError(
            f"[[lists]] name '{name}': use letters, digits, '_', '-', '.' or '+'"
        )
    scan_range = _required(table, "scan_range", list, name)
    if len(scan_range) != 2:
        raise ExclusionListError(f"{name}: scan_range is [low, high], e.g. [145, 800]")
    low, high = (_positive(value, f"{name} scan_range") for value in scan_range)
    if low >= high:
        raise ExclusionListError(f"{name}: scan_range low must be below high")
    tolerance = _positive(
        table.get("exclusion_tolerance_ppm", DEFAULT_EXCLUSION_TOLERANCE_PPM),
        f"{name} exclusion_tolerance_ppm",
    )
    rt = _required(table, "rt", bool, name)
    return ListSpec(name, (low, high), tolerance, rt)


def _settings(table: Any) -> MethodSettings:
    if not isinstance(table, dict):
        raise ExclusionListError("[method] must be a table")
    _only_keys(
        table,
        {"intensity_threshold", "inclusion_tolerance_ppm", "max_entries"},
        "[method]",
    )
    return MethodSettings(
        intensity_threshold=_positive(
            table.get("intensity_threshold", DEFAULT_SETTINGS.intensity_threshold),
            "[method] intensity_threshold",
        ),
        inclusion_tolerance_ppm=_positive(
            table.get(
                "inclusion_tolerance_ppm", DEFAULT_SETTINGS.inclusion_tolerance_ppm
            ),
            "[method] inclusion_tolerance_ppm",
        ),
        max_entries=require_integer(
            table.get("max_entries", DEFAULT_SETTINGS.max_entries),
            field="[method] max_entries",
            positive=True,
            error_factory=ExclusionListError,
        ),
    )


def _positive(value: Any, field: str) -> float:
    return require_finite_real(
        value, field=field, positive=True, error_factory=ExclusionListError
    )


def _check_input_path(path: Path, outputs: set[Path]) -> None:
    if path.resolve() in outputs:
        raise ExclusionListError(
            f"output would overwrite input {path}; use --output-dir"
        )


def _matching_files(base: Path, pattern: Any, outputs: set[Path]) -> list[Path]:
    if not isinstance(pattern, str):
        raise ExclusionListError("targets: list file paths or patterns as text")
    # Check even on the first run, before the output exists: otherwise a second
    # run of the same recipe would read its previous exclusions as targets.
    absolute_pattern = str((base / pattern).resolve())
    if any(output.match(absolute_pattern) for output in outputs):
        raise ExclusionListError(
            f"targets '{pattern}' matches an output; use --output-dir"
        )
    files = sorted(Path(p) for p in glob.glob(str(base / pattern)))
    for file in files:
        _check_input_path(file, outputs)
    if not files:
        raise ExclusionListError(f"targets: nothing matches '{pattern}'")
    return files


def _csv_rows(path: Path) -> list[list[str]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.reader(handle))
    except (OSError, UnicodeDecodeError) as exc:
        raise ExclusionListError(f"{path.name}: {exc}") from exc


def _sheet_rows(path: Path, sheet: str | None) -> list[tuple[Any, ...]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise ExclusionListError(
                f"{path.name}: no sheet '{sheet}' "
                f"(has {', '.join(workbook.sheetnames)})"
            )
        return list(workbook[sheet].iter_rows(values_only=True))
    finally:
        workbook.close()


def _optional_index(header: list[str], name: str) -> int | None:
    return header.index(name) if name in header else None


def _number(value: Any, where: str) -> float:
    number = optional_float(value)
    if number is None:
        raise ExclusionListError(f"{where}: '{value}' is not a finite number")
    return number


def _required(table: dict[str, Any], key: str, kind: type, where: str) -> Any:
    if key not in table:
        raise ExclusionListError(f"{where}: missing '{key}'")
    value = table[key]
    if not isinstance(value, kind):
        raise ExclusionListError(f"{where}: '{key}' has the wrong type")
    return value


def _only_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ExclusionListError(f"{where}: unknown key(s) {', '.join(unknown)}")
